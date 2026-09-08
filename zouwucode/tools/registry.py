"""Tool registry — manages all available tools and their schemas."""

from typing import Optional

from .base import BaseTool


class ToolRegistry:
    """Central registry for all tools.

    Tools are registered once at session start and their schemas are
    frozen to maintain byte-stable prefix for cache optimization.
    """

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
        self._frozen_schemas: Optional[list[dict]] = None

    def register(self, tool: BaseTool) -> None:
        """Register a tool."""
        self._tools[tool.get_name()] = tool
        self._frozen_schemas = None  # invalidate cache

    def register_all(self, tools: list[BaseTool]) -> None:
        """Register multiple tools at once."""
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> Optional[BaseTool]:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_schemas(self) -> list[dict]:
        """Get all tool schemas (frozen/cached for stability)."""
        if self._frozen_schemas is None:
            self._frozen_schemas = [
                tool.get_spec().to_openai_tool()
                for tool in self._tools.values()
            ]
        return list(self._frozen_schemas)

    def get_names(self) -> list[str]:
        """Get all registered tool names."""
        return list(self._tools.keys())

    async def execute(self, name: str, **kwargs):
        """Execute a tool by name."""
        tool = self.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")
        return await tool.execute(**kwargs)