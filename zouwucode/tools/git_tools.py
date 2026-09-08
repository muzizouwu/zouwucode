"""Git operation tools."""

import asyncio
import shlex
import sys
from pathlib import Path
from typing import Optional

from .base import BaseTool, ToolSpec, ToolResult


class GitTool(BaseTool):
    """Execute git commands."""

    DEFAULT_TIMEOUT = 120  # clone/fetch can legitimately take a while

    def get_spec(self) -> ToolSpec:
        return ToolSpec(
            name="git",
            description="Execute a git command. Supports status, diff, commit, log, branch, etc.",
            parameters={
                "args": {
                    "type": "string",
                    "description": "Git arguments (e.g. 'status', 'diff', 'log --oneline -5')",
                },
                "timeout": {
                    "type": "integer",
                    "description": f"Timeout in seconds (default: {self.DEFAULT_TIMEOUT})",
                },
                "workdir": {
                    "type": "string",
                    "description": "Working directory (default: current)",
                },
            },
            required=["args"],
        )

    async def execute(
        self,
        args: str,
        timeout: int = DEFAULT_TIMEOUT,
        workdir: Optional[str] = None,
    ) -> ToolResult:
        # Sandbox check: git operations can be disabled entirely
        if self.sandbox:
            allowed = await self.sandbox.check_git()
            if not allowed:
                return ToolResult(
                    success=False,
                    output="",
                    error="Git operations are disabled by sandbox config.",
                )

        try:
            cwd = Path(workdir).resolve() if workdir else None

            # shlex keeps quoted arguments intact ('commit -m "fix: bug"');
            # posix=False matches Windows quoting conventions.
            argv = shlex.split(args, posix=(sys.platform != "win32"))
            if not argv:
                return ToolResult(success=False, output="", error="Empty git arguments")

            proc = await asyncio.create_subprocess_exec(
                "git",
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()  # don't leak a background git process
                await proc.wait()
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Git command timed out after {timeout}s",
                )

            output = stdout.decode("utf-8", errors="replace")
            error = stderr.decode("utf-8", errors="replace")

            if proc.returncode != 0:
                return ToolResult(success=False, output=output, error=error)

            return ToolResult(success=True, output=output)

        except ValueError as e:
            return ToolResult(success=False, output="", error=f"Invalid arguments: {e}")
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
