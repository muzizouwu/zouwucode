"""Abstract base provider for LLM API communication."""

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional


class ProviderAPIError(RuntimeError):
    """LLM API error carrying its HTTP status for retry classification.

    status_code == 0 means a transport-level failure (connection reset,
    timeout, DNS) — always treated as transient by the engine's retry logic.
    """

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class Message:
    """A single message in the conversation."""

    def __init__(self, role: str, content: str, tool_calls: Optional[list] = None):
        self.role = role
        self.content = content
        self.tool_calls = tool_calls or []

    def to_dict(self) -> dict:
        d = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        return d


class ToolCall:
    """A tool invocation requested by the model."""

    def __init__(self, id: str, name: str, arguments: str):
        self.id = id
        self.name = name
        self.arguments = arguments

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


class ToolResult:
    """Result returned from a tool execution."""

    def __init__(self, tool_call_id: str, content: str, is_error: bool = False):
        self.tool_call_id = tool_call_id
        self.content = content
        self.is_error = is_error

    def to_dict(self) -> dict:
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": self.content,
        }


class ModelResponse:
    """Parsed response from the LLM."""

    def __init__(
        self,
        content: str = "",
        tool_calls: Optional[list[ToolCall]] = None,
        thinking: str = "",
        usage: Optional[dict] = None,
        cache_hit: bool = False,
    ):
        self.content = content
        self.tool_calls = tool_calls or []
        self.thinking = thinking
        self.usage = usage or {}
        self.cache_hit = cache_hit


class BaseProvider(ABC):
    """Abstract base class for LLM providers."""

    def __init__(self, config: dict):
        self.api_key = config.get("api_key", "")
        self.base_url = config.get("base_url", "")
        self.model = config.get("model", "")
        self.api_type = config.get("api_type", "deepseek")

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.0,
        max_tokens: int = 65536,
        stream_thinking: bool = True,
    ) -> AsyncIterator[ModelResponse]:
        """Stream a chat completion.

        Yields *incremental* ModelResponse objects: each chunk carries only the
        newly arrived text (``content`` and/or ``thinking`` deltas). The final
        yielded object is the complete response with ``content``/``thinking``
        accumulated plus any ``tool_calls``/``usage``/``cache_hit``.
        """
        ...

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.0,
        max_tokens: int = 65536,
    ) -> ModelResponse:
        """Non-streaming chat completion."""
        ...

    def get_cache_key(self, messages: list[dict]) -> str:
        """Return a deterministic cache key for the given messages prefix."""
        import hashlib
        raw = "".join(f"{m['role']}:{m['content']}" for m in messages)
        return hashlib.sha256(raw.encode()).hexdigest()