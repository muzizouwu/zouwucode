"""LSP extension — diagnostics integration space (reserved).

Inactive until ``extensions.lsp_enabled: true``. When enabled, contributes
a :class:`CheckDiagnosticsTool` that wraps the existing
:class:`~zouwucode.lsp.client.LSPClient`: the model can ask for language-
server diagnostics of a file (used for self-healing after edits).
"""

import logging
from typing import Optional

from ..tools.base import BaseTool, ToolResult, ToolSpec
from .host import Extension, ExtensionContext

logger = logging.getLogger("zouwucode.extensions.lsp")


class CheckDiagnosticsTool(BaseTool):
    """Fetch language-server diagnostics for a source file."""

    def __init__(self, lsp_client):
        super().__init__(sandbox=None)
        self._lsp = lsp_client

    def get_spec(self) -> ToolSpec:
        return ToolSpec(
            name="check_diagnostics",
            description=(
                "Run language-server diagnostics (LSP) on a source file and "
                "return errors/warnings. Supported: Python, JS/TS, Rust, Go, "
                "Java, C/C++. Requires the corresponding language server to "
                "be installed on PATH."
            ),
            parameters={
                "file_path": {"type": "string", "description": "Path to the source file"},
                "content": {
                    "type": "string",
                    "description": "Optional file content to check (defaults to reading nothing)",
                },
            },
            required=["file_path"],
        )

    async def execute(self, file_path: str, content: str = "", **_) -> ToolResult:
        try:
            diagnostics = await self._lsp.get_diagnostics(file_path, content)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output="", error=str(exc))
        if not diagnostics:
            return ToolResult(success=True, output="No diagnostics — file is clean.")
        lines = [
            f"  [{d.get('severity', '?')}] {d.get('message', '')}"
            for d in diagnostics
        ]
        return ToolResult(success=True, output=f"{len(diagnostics)} finding(s):\n" + "\n".join(lines))


class LspExtension(Extension):
    """Exposes LSP diagnostics as a tool when enabled."""

    name = "lsp"

    def __init__(self, enabled: bool = False):
        self._enabled = enabled
        self._client = None
        self._tools: list[BaseTool] = []

    async def start(self, ctx: ExtensionContext) -> None:
        if not self._enabled:
            logger.info("LSP extension: disabled — inactive.")
            return
        from ..lsp.client import LSPClient

        self._client = LSPClient()
        self._tools.append(CheckDiagnosticsTool(self._client))
        logger.info("LSP extension contributed %d tool(s).", len(self._tools))

    async def stop(self) -> None:
        self._client = None
        self._tools.clear()

    def get_tools(self) -> list[BaseTool]:
        return list(self._tools)
