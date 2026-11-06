"""Tool system — permission-gated file, shell, git, web, and MCP tools."""

from .base import BaseTool, ToolResult, ToolSpec
from .file_tools import EditTool, GlobTool, LsTool, ReadTool, WriteTool
from .git_tools import GitTool
from .registry import ToolRegistry
from .search_tools import GrepTool
from .shell_tools import ShellTool
from .web_tools import WebFetchTool, WebSearchTool

__all__ = [
    "BaseTool", "ToolResult", "ToolSpec",
    "ToolRegistry",
    "ReadTool", "WriteTool", "EditTool", "LsTool", "GlobTool", "GrepTool",
    "ShellTool",
    "GitTool",
    "WebSearchTool", "WebFetchTool",
]
