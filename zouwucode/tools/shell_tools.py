"""Shell execution tool with sandbox integration."""

import asyncio
import sys
import shlex
from pathlib import Path
from typing import Optional

from .base import BaseTool, ToolSpec, ToolResult


class ShellTool(BaseTool):
    """Execute shell commands with sandbox restrictions."""

    def get_spec(self) -> ToolSpec:
        return ToolSpec(
            name="bash",
            description="Execute a shell command. Returns stdout and stderr.",
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
            cwd = Path(workdir).resolve() if workdir else None

            # Use PowerShell on Windows, bash elsewhere
            if sys.platform == "win32":
                executable = "powershell"
                args = ["-NoProfile", "-Command", command]
            else:
                executable = "bash"
                args = ["-c", command]

            proc = await asyncio.create_subprocess_exec(
                executable,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
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

            output = stdout.decode("utf-8", errors="replace")
            error = stderr.decode("utf-8", errors="replace")

            if proc.returncode != 0:
                return ToolResult(
                    success=False,
                    output=output,
                    error=error or f"Exit code: {proc.returncode}",
                )

            return ToolResult(success=True, output=output)

        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))