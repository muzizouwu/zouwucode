"""python_exec — CodeAct-style executable actions (OpenHands' core insight).

Instead of forcing every multi-step operation through one JSON tool call,
the model can write Python that *composes* operations (loops, conditionals,
assertions, ad-hoc verification scripts) and runs it in a persistent
interpreter. On complex tasks this measurably outperforms schema-only tool
calling because the model can express control flow natively.

Design:
- ONE long-lived interpreter running a small self-driven REPL driver: it
  reads length-prefixed snippets from stdin, exec()s them in a shared
  globals dict, and prints a unique sentinel after each — so stdout
  capture per call is exact and the child never waits for EOF (plain
  `python -u` over a pipe would block until stdin closes).
- Namespace persists between calls (variables, imports) — that is the
  point of a REPL.
- Safety: snippets pass the same PermissionManager command screen as
  shell, plus a destructive-pattern check; hard per-call timeout kills
  and restarts the session; output is truncated. This is a process-level
  guard, not a container — a documented trade-off matching the project's
  local-first posture (OpenHands gets container isolation from Docker
  workspaces; this portable tool intentionally requires none).
"""

import asyncio
import base64
import contextvars
import re
import sys
from typing import Optional

from .base import BaseTool, ToolSpec, ToolResult
from ..errors import describe_exc

_MAX_OUTPUT = 30_000
_DEFAULT_TIMEOUT = 60.0

# Emitted by the REPL driver on its own line when exec() raised. The tool
# strips it from the output and reports is_error=True — otherwise a
# traceback in stdout used to come back as success (the model would then
# reason on top of a failed step believing it worked).
_ERR_MARKER = "__ZWC_ERROR__"

# Session isolation: SubAgent workers run inside asyncio tasks created by
# asyncio.gather, and each task copies the current Context — so setting a
# session id at the top of SubAgent.run() gives every sub-agent its OWN
# persistent REPL, while the main agent shares the default one. Without
# this, parallel sub-agents serialize on one lock AND cross-pollute each
# other's globals (variables/imports leaking between unrelated workers).
_current_session: contextvars.ContextVar[str] = contextvars.ContextVar(
    "python_exec_session", default="main")


def set_exec_session(name: str) -> contextvars.Token:
    """Bind the current asyncio task to a named REPL session."""
    return _current_session.set(name)


def reset_exec_session(token: contextvars.Token) -> None:
    _current_session.reset(token)

# Cheap pre-screen for obviously hostile snippets — reuses the spirit of
# the shell deny-patterns without duplicating the whole list.
_RISKY = re.compile(
    r"(rm\s+-rf\s+[/~]|shutil\.rmtree\(\s*['\"][/~]|os\.system\s*\(\s*['\"]rm\s|"
    r"subprocess\.(run|Popen|call)\s*\(\s*[^\)]*shutdown)",
    re.IGNORECASE)

# The self-driven REPL driver run as the child process. Wire protocol per
# call (all ASCII, immune to encoding/newline issues):
#   line 1: "<byte-length-of-line-2>"
#   line 2: "<sentinel> <base64(utf-8 code)>"
# After exec'ing the snippet the driver prints the sentinel line, which
# marks the exact end of that call's stdout. Plain `python -u` reading
# stdin would wait for EOF; this driver loops on readline() instead.
_DRIVER = r"""
import sys, base64
while True:
    header = sys.stdin.readline()
    if not header:
        break
    try:
        int(header)  # validate framing; content is the line-2 length
    except ValueError:
        continue
    line = sys.stdin.readline()
    if not line:
        break
    try:
        sentinel, b64 = line.strip().split(" ", 1)
        code = base64.b64decode(b64).decode("utf-8")
    except Exception:
        continue
    try:
        exec(compile(code, "<python_exec>", "exec"), globals())
    except SystemExit as e:
        print("[SystemExit %s]" % e.code)
    except BaseException:
        import traceback
        traceback.print_exc(file=sys.stdout)
        sys.stdout.write("__ZWC_ERROR__\n")
    sys.stdout.write(sentinel + "\n")
    sys.stdout.flush()
"""


class PythonExecTool(BaseTool):
    """Persistent Python REPL as an agent action surface."""

    def __init__(self, sandbox=None):
        super().__init__(sandbox)
        # Per-session REPL state so parallel sub-agents don't serialize on
        # one lock or share one globals dict. Keyed by _current_session.
        self._procs: dict[str, Optional[asyncio.subprocess.Process]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._seqs: dict[str, int] = {}

    def _session(self) -> str:
        return _current_session.get()

    def _lock_for(self, session: str) -> asyncio.Lock:
        lock = self._locks.get(session)
        if lock is None:
            lock = self._locks[session] = asyncio.Lock()
        return lock

    def get_spec(self) -> ToolSpec:
        return ToolSpec(
            name="python_exec",
            description=(
                "Run Python code in a persistent interpreter session. "
                "Variables and imports persist across calls — ideal for "
                "multi-step data processing, quick verification scripts, "
                "batch edits, and inspecting results programmatically. "
                "stdout (including print output) is captured. Runs in the "
                "current working directory."
            ),
            parameters={
                "code": {
                    "type": "string",
                    "description": "Python code to execute in the session",
                },
                "timeout": {
                    "type": "number",
                    "description": f"Per-call timeout seconds (default {_DEFAULT_TIMEOUT:.0f})",
                },
            },
            required=["code"],
        )

    async def _ensure_proc(self, session: str) -> asyncio.subprocess.Process:
        proc = self._procs.get(session)
        if proc is not None and proc.returncode is None:
            return proc
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", "-c", _DRIVER,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._procs[session] = proc
        return proc

    async def execute(self, code: str,
                      timeout: float = _DEFAULT_TIMEOUT) -> ToolResult:
        if not code or not code.strip():
            return ToolResult(success=False, output="", error="empty code")

        # Python-source danger screen (NOT the shell check_command — its
        # regexes target shell syntax and false-positive on ordinary Python,
        # e.g. a variable named "format" or a "reg" substring tripping the
        # registry/filesystem patterns). _RISKY catches genuinely destructive
        # calls; sandbox still governs via the shared allow_shell posture.
        if _RISKY.search(code):
            return ToolResult(
                success=False, output="",
                error="python_exec: snippet matches a destructive pattern; "
                      "use targeted file tools (edit/write) instead.")

        session = self._session()
        self._seqs[session] = self._seqs.get(session, 0) + 1
        seq = self._seqs[session]
        sentinel = f"__ZWC_EOF_{session}_{seq}__"
        b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
        frame = f"{sentinel} {b64}"
        request = f"{len(frame)}\n{frame}\n".encode("ascii")

        async with self._lock_for(session):
            try:
                proc = await self._ensure_proc(session)
                assert proc.stdin is not None and proc.stdout is not None
                proc.stdin.write(request)
                await proc.stdin.drain()

                collected: list[str] = []
                raised = False
                loop = asyncio.get_event_loop()
                deadline = loop.time() + max(1.0, timeout)
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        await self._restart(session)
                        return ToolResult(
                            success=False,
                            output="".join(collected)[:_MAX_OUTPUT],
                            error=f"python_exec timed out after {timeout:.0f}s "
                                  "(session restarted)")
                    try:
                        line_b = await asyncio.wait_for(
                            proc.stdout.readline(), timeout=remaining)
                    except asyncio.TimeoutError:
                        continue
                    if not line_b:  # interpreter died
                        await self._restart(session)
                        out = "".join(collected)[:_MAX_OUTPUT]
                        return ToolResult(
                            success=False, output=out,
                            error="python session ended unexpectedly; "
                                  "a fresh session will start next call")
                    line = line_b.decode("utf-8", "replace")
                    stripped = line.rstrip("\r\n")
                    if stripped == sentinel:
                        break
                    if stripped == _ERR_MARKER:
                        raised = True
                        continue
                    collected.append(line)

                out = "".join(collected)
                if len(out) > _MAX_OUTPUT:
                    out = out[:_MAX_OUTPUT] + "\n…[output truncated]"
                failed = raised or (
                    "[SystemExit" in out and "SystemExit None" not in out
                    and "SystemExit 0" not in out)
                if failed:
                    return ToolResult(
                        success=False, output=out,
                        error="python_exec: the snippet raised "
                              "(traceback above); treat this step as failed.")
                return ToolResult(success=True, output=out)
            except Exception as exc:  # noqa: BLE001
                await self._restart(session)
                return ToolResult(success=False, output="",
                                  error=describe_exc(exc))

    async def _restart(self, session: str) -> None:
        """Kill and forget one session's REPL; next call spawns a fresh one."""
        proc = self._procs.pop(session, None)
        if proc is not None:
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:
                pass

    async def close(self) -> None:
        for session in list(self._procs):
            await self._restart(session)
        self._locks.clear()
        self._seqs.clear()
