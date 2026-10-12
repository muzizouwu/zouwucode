"""LSP extension — diagnostics integration space (reserved).

Inactive until ``extensions.lsp_enabled: true``. When enabled, contributes
a :class:`CheckDiagnosticsTool` that wraps the existing
:class:`~zouwucode.lsp.client.LSPClient`: the model can ask for language-
server diagnostics of a file (used for self-healing after edits).
"""

import logging
from typing import Optional

from ..tools.base import BaseTool, ToolResult, ToolSpec
from ..errors import describe_exc
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
            return ToolResult(success=False, output="",
                              error=describe_exc(exc))
        if not diagnostics:
            return ToolResult(success=True, output="No diagnostics — file is clean.")
        lines = [
            f"  [{d.get('severity', '?')}] {d.get('message', '')}"
            for d in diagnostics
        ]
        return ToolResult(success=True, output=f"{len(diagnostics)} finding(s):\n" + "\n".join(lines))


class LspHoverTool(BaseTool):
    """Show what a symbol at a position is (type / docs) via LSP."""

    def __init__(self, lsp_client):
        super().__init__(sandbox=None)
        self._lsp = lsp_client

    def get_spec(self) -> ToolSpec:
        return ToolSpec(
            name="lsp_hover",
            description=(
                "Show hover information (type, signature, docs) for the symbol "
                "at a 1-based line/column in a source file, via the language "
                "server. Use it to understand unfamiliar code before editing. "
                "Requires the corresponding language server on PATH."
            ),
            parameters={
                "file_path": {"type": "string", "description": "Path to the source file"},
                "line": {"type": "integer", "description": "1-based line number"},
                "column": {"type": "integer", "description": "1-based column (default 1)"},
            },
            required=["file_path", "line"],
        )

    async def execute(self, file_path: str, line: int = 1, column: int = 1, **_) -> ToolResult:
        try:
            text = await self._lsp.hover(file_path, int(line) - 1, max(0, int(column) - 1))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output="", error=describe_exc(exc))
        if not text:
            return ToolResult(success=True, output="No hover information at that position.")
        return ToolResult(success=True, output=text)


class LspDefinitionTool(BaseTool):
    """Find where a symbol is declared (go-to-definition) via LSP."""

    def __init__(self, lsp_client):
        super().__init__(sandbox=None)
        self._lsp = lsp_client

    def get_spec(self) -> ToolSpec:
        return ToolSpec(
            name="lsp_definition",
            description=(
                "Find the definition(s) of the symbol at a 1-based line/column "
                "in a source file, via the language server — the fastest way to "
                "locate where something is declared. Requires the language "
                "server on PATH."
            ),
            parameters={
                "file_path": {"type": "string", "description": "Path to the source file"},
                "line": {"type": "integer", "description": "1-based line number"},
                "column": {"type": "integer", "description": "1-based column (default 1)"},
            },
            required=["file_path", "line"],
        )

    async def execute(self, file_path: str, line: int = 1, column: int = 1, **_) -> ToolResult:
        try:
            defs = await self._lsp.definition(file_path, int(line) - 1, max(0, int(column) - 1))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output="", error=describe_exc(exc))
        if not defs:
            return ToolResult(success=True, output="No definition found at that position.")
        lines = [f"  {d['file']}:{d['line'] + 1}:{d['character'] + 1}" for d in defs]
        return ToolResult(success=True, output="Definition(s):\n" + "\n".join(lines))


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
        self._tools.append(LspHoverTool(self._client))
        self._tools.append(LspDefinitionTool(self._client))
        logger.info("LSP extension contributed %d tool(s).", len(self._tools))

    async def stop(self) -> None:
        self._client = None
        self._tools.clear()

    def get_tools(self) -> list[BaseTool]:
        return list(self._tools)
