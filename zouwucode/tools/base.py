"""Base tool definition and result types."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolSpec:
    """Specification of a tool for LLM function calling."""

    name: str
    description: str
    parameters: dict  # JSON Schema
    required: list[str] = field(default_factory=list)

    def to_openai_tool(self) -> dict:
        """Convert to OpenAI-compatible tool definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": self.required,
                },
            },
        }


@dataclass
class ToolResult:
    """Result of a tool execution."""

    success: bool
    output: str
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
        }


class BaseTool(ABC):
    """Abstract base class for all tools."""

    def __init__(self, sandbox: Optional[Any] = None):
        self.sandbox = sandbox

    @abstractmethod
    def get_spec(self) -> ToolSpec:
        """Return the tool specification for LLM function calling."""
        ...

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with the given arguments."""
        ...

    def get_name(self) -> str:
        return self.get_spec().name