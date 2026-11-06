"""Extension layer — reserved integration space for MCP / LSP services."""

from .host import Extension, ExtensionContext, ExtensionHost
from .lsp_ext import CheckDiagnosticsTool, LspExtension
from .mcp_ext import McpExtension, MCPTool

__all__ = [
    "Extension",
    "ExtensionContext",
    "ExtensionHost",
    "McpExtension",
    "MCPTool",
    "LspExtension",
    "CheckDiagnosticsTool",
]
