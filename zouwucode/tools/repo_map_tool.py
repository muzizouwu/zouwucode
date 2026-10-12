"""repo_map tool — on-demand, ranked codebase overview (Aider-style)."""

from pathlib import Path
from typing import Optional

from .base import BaseTool, ToolSpec, ToolResult
from ..errors import describe_exc
from ..repo_map import build_repo_map


class RepoMapTool(BaseTool):
    """Return a compact, ranked map of the codebase (signatures only)."""

    def get_spec(self) -> ToolSpec:
        return ToolSpec(
            name="repo_map",
            description=(
                "Get a compact, ranked map of the codebase (files + their "
                "class/function signatures), ordered by how often each file is "
                "referenced. Use this FIRST on an unfamiliar project instead of "
                "reading many files — it is cheap and saves exploration rounds."
            ),
            parameters={
                "path": {"type": "string",
                         "description": "Root directory (default: workspace root)"},
                "max_tokens": {"type": "integer",
                               "description": "Approximate token budget (default 1024)"},
            },
        )

    async def execute(self, path: str = ".", max_tokens: int = 1024) -> ToolResult:
        try:
            root = Path(path).resolve()
            checker = getattr(self.sandbox, "check_file_access", None) if self.sandbox else None
            if checker is not None:
                allowed = await checker(str(root))
                if not allowed:
                    return ToolResult(success=False, output="",
                                      error=f"Path outside allowed workspace: {root}")
            out = build_repo_map(root, max_tokens=max(256, int(max_tokens or 1024)))
            if not out:
                return ToolResult(success=True, output="(no source files found)")
            return ToolResult(success=True, output=out)
        except Exception as e:
            return ToolResult(success=False, output="", error=describe_exc(e))
