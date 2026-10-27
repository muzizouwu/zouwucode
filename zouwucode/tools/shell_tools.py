"""Shell execution tool with sandbox integration."""

import logging

logger = logging.getLogger("zouwucode.tools.shell")

import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Optional

from .base import BaseTool, ToolSpec, ToolResult
from ._encoding import PS_UTF8_PREFIX, decode_console, utf8_env
from ..errors import describe_exc

# POSIX-isms that Windows PowerShell 5.x cannot parse ('&&' is a hard
# syntax error there). When we spot these on Windows we prefer a real
# bash (Git for Windows) if one exists.
_POSIX_HINT = re.compile(
    r"&&|\|\||2>\s*/dev/null|\$\(|\bwhich\b|\bexport\s+\w+=")

_PS_SYNTAX_NOTE = (
    "\n[hint] This ran under Windows PowerShell 5.x, which does not accept "
    "POSIX syntax ('&&', '||', '2>/dev/null', '$(...)'). Use ';' to chain "
    "commands, or PowerShell-native syntax. If bash semantics are required, "
    "install Git for Windows (git.exe's bundled bash is auto-detected)."
)

# A runaway command (test suites, log dumps, `cat` on a huge file) must not
# dump megabytes into the prompt: the model cannot read it and the cost is
# billed. Keep the head (where the cause usually is) and the tail (where the
# verdict usually is), with an explicit marker for what was dropped.
_MAX_STREAM_CHARS = 30_000
_STREAM_HEAD = 20_000
_STREAM_TAIL = 10_000
_MAX_TIMEOUT_SECONDS = 3600


def _cap_stream(text: str) -> str:
    """Truncate one output stream to a context-safe size."""
    if len(text) <= _MAX_STREAM_CHARS:
        return text
    dropped = len(text) - _STREAM_HEAD - _STREAM_TAIL
    return (text[:_STREAM_HEAD]
            + f"\n… (output truncated: {dropped} chars omitted out of "
              f"{len(text)} total — rerun with a narrower command, e.g. "
              f"redirect to a file and grep it)\n"
            + text[-_STREAM_TAIL:])


def _find_git_bash() -> Optional[str]:
    """Locate Git-for-Windows bash.exe (cheap, cached)."""
    if sys.platform != "win32":
        return None
    cached = _find_git_bash.__dict__.get("path", "__unset__")
    if cached != "__unset__":
        return cached
    found: Optional[str] = None
    # 1) git.exe on PATH → <root>/usr/bin/bash.exe (robust to install path)
    import shutil
    git = shutil.which("git")
    if git:
        root = Path(git).resolve()
        for up in (root.parents):
            cand = up / "usr" / "bin" / "bash.exe"
            if cand.exists():
                found = str(cand)
                break
    # 2) common fixed locations
    if found is None:
        for cand in (r"C:\Program Files\Git\usr\bin\bash.exe",
                     r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
                     os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\usr\bin\bash.exe")):
            if Path(cand).exists():
                found = cand
                break
    _find_git_bash.__dict__["path"] = found
    return found


class ShellTool(BaseTool):
    """Execute shell commands with sandbox restrictions.

    When a ``ContainerSandbox`` is supplied and a container runtime is present,
    commands run inside a throwaway container (only the workspace mounted, no
    network by default) — real isolation, not just a regex blacklist. If the
    runtime is unavailable the tool transparently falls back to the host shell.
    """

    def __init__(self, sandbox=None, container=None):
        super().__init__(sandbox)
        self.container = container

    def _use_container(self) -> bool:
        """True when a container runtime is configured AND available."""
        return bool(self.container is not None and self.container.available())

    def get_spec(self) -> ToolSpec:
        platform = ("Windows PowerShell" if sys.platform == "win32"
                    else "bash")
        return ToolSpec(
            name="bash",
            description=(
                "Execute a shell command. Returns stdout and stderr. "
                f"Current host shell: {platform}."
                + (" PowerShell 5.x does NOT support '&&'/'||' — chain with "
                   "';' or run POSIX commands as a single bash-style string "
                   "(a detected Git Bash is used automatically)."
                   if sys.platform == "win32" else "")
            ),
            parameters={
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 30)",
                },
                "workdir": {
                    "type": "string",
                    "description": "Working directory (default: current)",
                },
            },
            required=["command"],
        )

    def _pick_executable(self, command: str):
        """Choose (executable, args) for the command."""
        if sys.platform == "win32":
            bash = _find_git_bash()
            if bash and _POSIX_HINT.search(command):
                return bash, ["-c", command]
            # PS_UTF8_PREFIX: Windows PowerShell 5.x writes redirected
            # stdout in the console code page (GBK on zh-CN) — forced UTF-8.
            return "powershell", ["-NoProfile", "-Command",
                                  PS_UTF8_PREFIX + command]
        return "bash", ["-c", command]

    async def execute(
        self,
        command: str,
        timeout: int = 30,
        workdir: Optional[str] = None,
    ) -> ToolResult:
        # Sandbox check: prevent dangerous commands
        if self.sandbox:
            allowed = await self.sandbox.check_command(command)
            if not allowed:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Command blocked by sandbox: {command}",
                )

        try:
            timeout = max(1, min(int(timeout or 30), _MAX_TIMEOUT_SECONDS))
        except (TypeError, ValueError):
            timeout = 30

        if self._use_container():
            return await self._run_in_container(command, timeout, workdir)

        try:
            cwd = Path(workdir).resolve() if workdir else None
            executable, args = self._pick_executable(command)

            proc = await asyncio.create_subprocess_exec(
                executable,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=utf8_env(),
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Command timed out after {timeout}s",
                )

            output = decode_console(stdout)
            error = decode_console(stderr)
            output = _cap_stream(output)
            error = _cap_stream(error)

            if proc.returncode != 0:
                # PowerShell 5 '&&' syntax error → teach the model the fix
                # (it otherwise retries the same failing call and looks
                # "stuck"). Only annotate when bash was NOT available.
                if (executable == "powershell"
                        and "not a valid statement separator" in error):
                    error += _PS_SYNTAX_NOTE
                return ToolResult(
                    success=False,
                    output=output,
                    error=error or f"Exit code: {proc.returncode}",
                )

            return ToolResult(success=True, output=output)

        except Exception as e:
            return ToolResult(success=False, output="", error=describe_exc(e))

    async def _run_in_container(self, command: str, timeout: int,
                                workdir=None) -> ToolResult:
        """Execute in the container; fall back to the host on runtime failure."""
        try:
            stdout, stderr, code = await self.container.run(
                command, timeout=timeout, workdir=workdir)
        except asyncio.TimeoutError:
            return ToolResult(success=False, output="",
                              error=f"Command timed out after {timeout}s "
                                    f"(container: {self.container.image})")
        except Exception as e:  # runtime/daemon error → degrade, don't fail hard
            logger.warning("container run failed (%s); falling back to host shell",
                           describe_exc(e))
            self.container = None  # avoid re-trying a broken runtime every call
            return await self.execute(command, timeout=timeout, workdir=workdir)

        output = decode_console(stdout)
        error = decode_console(stderr)
        output = _cap_stream(output)
        error = _cap_stream(error)
        if code != 0:
            return ToolResult(success=False, output=output,
                              error=error or f"Exit code: {code}")
        return ToolResult(success=True, output=output)
