"""Tool system — permission-gated file, shell, git, web, and MCP tools."""

from .base import BaseTool, ToolResult, ToolSpec
from .registry import ToolRegistry
from .file_tools import ReadTool, WriteTool, EditTool, LsTool, GlobTool
from .search_tools import GrepTool
from .shell_tools import ShellTool
from .git_tools import GitTool
from .web_tools import WebSearchTool, WebFetchTool

__all__ = [
    "BaseTool", "ToolResult", "ToolSpec",
    "ToolRegistry",
    "ReadTool", "WriteTool", "EditTool", "LsTool", "GlobTool", "GrepTool",
    "ShellTool",
    "GitTool",
    "WebSearchTool", "WebFetchTool",
]
