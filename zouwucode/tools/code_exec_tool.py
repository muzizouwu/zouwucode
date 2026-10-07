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
import re
import sys
from typing import Optional

from .base import BaseTool, ToolSpec, ToolResult

_MAX_OUTPUT = 30_000
_DEFAULT_TIMEOUT = 60.0

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
    sys.stdout.write(sentinel + "\n")
    sys.stdout.flush()
"""


class PythonExecTool(BaseTool):
    """Persistent Python REPL as an agent action surface."""

    def __init__(self, sandbox=None):
        super().__init__(sandbox)
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()
        self._seq = 0

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

    async def _ensure_proc(self) -> asyncio.subprocess.Process:
        if self._proc is not None and self._proc.returncode is None:
            return self._proc
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", "-c", _DRIVER,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        return self._proc

    async def execute(self, code: str,
                      timeout: float = _DEFAULT_TIMEOUT) -> ToolResult:
        if not code or not code.strip():
            return ToolResult(success=False, output="", error="empty code")

        if self.sandbox:
            allowed = await self.sandbox.check_command(code)
            if not allowed:
                return ToolResult(
                    success=False, output="",
                    error="Code blocked by sandbox policy")
        if _RISKY.search(code):
            return ToolResult(
                success=False, output="",
                error="python_exec: snippet matches a destructive pattern; "
                      "use targeted file tools (edit/write) instead.")

        self._seq += 1
        sentinel = f"__ZWC_EOF_{self._seq}__"
        b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
        frame = f"{sentinel} {b64}"
        request = f"{len(frame)}\n{frame}\n".encode("ascii")

        async with self._lock:
            try:
                proc = await self._ensure_proc()
                assert proc.stdin is not None and proc.stdout is not None
                proc.stdin.write(request)
                await proc.stdin.drain()

                collected: list[str] = []
                loop = asyncio.get_event_loop()
                deadline = loop.time() + max(1.0, timeout)
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        await self._restart()
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
                        await self._restart()
                        out = "".join(collected)[:_MAX_OUTPUT]
                        return ToolResult(
                            success=False, output=out,
                            error="python session ended unexpectedly; "
                                  "a fresh session will start next call")
                    line = line_b.decode("utf-8", "replace")
                    if line.rstrip("\r\n") == sentinel:
                        break
                    collected.append(line)

                out = "".join(collected)
                if len(out) > _MAX_OUTPUT:
                    out = out[:_MAX_OUTPUT] + "\n…[output truncated]"
                failed = "[SystemExit" in out and "SystemExit None" not in out \
                    and "SystemExit 0" not in out
                return ToolResult(success=not failed, output=out)
            except Exception as exc:  # noqa: BLE001
                await self._restart()
                return ToolResult(success=False, output="", error=str(exc))

    async def _restart(self) -> None:
        """Kill and forget the session; next execute() spawns a fresh one."""
        if self._proc is not None:
            try:
                self._proc.kill()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except Exception:
                pass
            self._proc = None

    async def close(self) -> None:
        await self._restart()
