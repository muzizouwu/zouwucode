"""Extension layer — reserved integration space for MCP / LSP services."""

from .host import Extension, ExtensionContext, ExtensionHost
from .mcp_ext import McpExtension, MCPTool
from .lsp_ext import LspExtension, CheckDiagnosticsTool

__all__ = [
    "Extension",
    "ExtensionContext",
    "ExtensionHost",
    "McpExtension",
    "MCPTool",
    "LspExtension",
    "CheckDiagnosticsTool",
]
