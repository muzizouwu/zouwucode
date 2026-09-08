"""DeepSeek-optimized provider with prefix-cache awareness."""

import json
from pathlib import Path
from typing import AsyncIterator, Optional

import httpx

import zouwucode

from .base import BaseProvider, ModelResponse, ProviderAPIError, ToolCall


class DeepSeekProvider(BaseProvider):
    """Provider tuned for DeepSeek API with prefix-cache optimization.

    Key design decisions for cache stability (Reasonix-inspired):
      1. System prompt is frozen at session start — never dynamically injected.
      2. Tool schemas are deterministic — same order, same descriptions.
      3. Messages are append-only — never reordered or compacted.
      4. File context is injected in consistent format and position.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        # Ensure base_url is never empty — fallback to DeepSeek default
        raw_url = config.get("base_url") or ""
        self.base_url = raw_url if raw_url.startswith("http") else "https://api.deepseek.com"
        self.model = config.get("model", "deepseek-v4-flash")
        self._frozen_system_prompt: Optional[str] = None
        self._frozen_tool_schemas: Optional[list[dict]] = None

    def freeze_system_prompt(self, prompt: str) -> None:
        """Freeze the system prompt for the entire session."""
        self._frozen_system_prompt = prompt

    def freeze_tool_schemas(self, schemas: list[dict]) -> None:
        """Freeze tool schemas so they are byte-identical every turn."""
        self._frozen_tool_schemas = schemas

    def set_reasoning_effort(self, level: str) -> None:
        """Set reasoning effort level.

        Maps ZOUWUCODE's three-tier abstraction to DeepSeek V4 API:
        - low:     No reasoning, deterministic output (temperature=0)
        - medium:  reasoning_effort="high" (default, balanced thinking)
        - max:     reasoning_effort="max" (maximum reasoning depth)
        """
        mapping = {
            "low": "low",
            "medium": "high",
            "max": "max",
        }
        self._reasoning_effort = mapping.get(level, "high")

    def _build_messages(self, messages: list[dict]) -> list[dict]:
        """Build the message list, avoiding duplicate system prompts."""
        # Check if messages already have a system prompt (from cache prefix)
        has_system = any(m.get("role") == "system" for m in messages)
        result = []
        if self._frozen_system_prompt and not has_system:
            result.append({"role": "system", "content": self._frozen_system_prompt})
        result.extend(messages)
        return result

    def _build_tools(self, tools: Optional[list[dict]]) -> Optional[list[dict]]:
        """Return frozen tool schemas if available, else the passed ones."""
        if self._frozen_tool_schemas is not None:
            return self._frozen_tool_schemas
        return tools

    async def chat_stream(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.0,
        max_tokens: int = 65536,
        stream_thinking: bool = True,
    ) -> AsyncIterator[ModelResponse]:
        api_messages = self._build_messages(messages)
        api_tools = self._build_tools(tools)

        payload = {
            "model": self.model,
            "messages": api_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if api_tools:
            payload["tools"] = api_tools
        if stream_thinking:
            payload["stream_options"] = {"include_usage": True}

        # Reasoning effort: low → no reasoning, medium → high, max → max
        # Maps ZOUWUCODE's three-tier abstraction to DeepSeek V4 API
        reasoning_effort = getattr(self, '_reasoning_effort', None)
        if reasoning_effort and reasoning_effort != "low":
            payload["reasoning_effort"] = reasoning_effort
        elif reasoning_effort == "low":
            # Low intensity: disable reasoning, use deterministic sampling
            payload.pop("stream_options", None)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        if not self.api_key:
            raise RuntimeError(
                "API key is not configured. "
                "Please set your API key in config.yaml:\n\n"
                "  providers:\n"
                "    deepseek:\n"
                "      api_key: \"sk-your-api-key\"\n\n"
                "Or pass it via command line:\n"
                "  zouwucode --api-key \"sk-your-api-key\"\n\n"
                "Config search path:\n"
                f"  1. {Path.cwd() / 'config.yaml'}\n"
                f"  2. {Path.cwd() / 'zouwucode_data' / 'config.yaml'}\n"
                f"  3. {Path(zouwucode.__file__).parent.parent / 'config.yaml'}"
            )

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            ) as resp:
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError:
                    error_body = await resp.aread()
                    raise ProviderAPIError(
                        f"DeepSeek API {resp.status_code}: {error_body.decode('utf-8', errors='replace')[:500]}",
                        status_code=resp.status_code,
                    )
                content_buf = ""
                thinking_buf = ""
                tool_calls_buf = {}
                usage = {}
                cache_hit = False

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices", [])
                    if not choices:
                        # Usage information may come in a separate chunk
                        if "usage" in chunk and chunk["usage"] is not None:
                            usage = chunk["usage"]
                        continue

                    delta = choices[0].get("delta", {})

                    # Thinking content (DeepSeek reasoning blocks) — incremental
                    if "reasoning_content" in delta and delta["reasoning_content"]:
                        think_delta = delta["reasoning_content"] or ""
                        thinking_buf += think_delta
                        yield ModelResponse(
                            content="", thinking=think_delta,
                            tool_calls=[], usage={}, cache_hit=cache_hit,
                        )

                    # Regular content — incremental
                    if delta.get("content"):
                        content_delta = delta["content"] or ""
                        content_buf += content_delta
                        yield ModelResponse(
                            content=content_delta, thinking="",
                            tool_calls=[], usage={}, cache_hit=cache_hit,
                        )

                    # Tool calls
                    if "tool_calls" in delta:
                        for tc in delta["tool_calls"]:
                            idx = tc.get("index", 0)
                            if idx not in tool_calls_buf:
                                tool_calls_buf[idx] = {
                                    "id": "",
                                    "function": {"name": "", "arguments": ""},
                                }
                            if tc.get("id"):
                                tool_calls_buf[idx]["id"] = tc["id"]
                            if tc.get("function", {}).get("name"):
                                tool_calls_buf[idx]["function"]["name"] += tc["function"]["name"]
                            if tc.get("function", {}).get("arguments"):
                                tool_calls_buf[idx]["function"]["arguments"] += tc["function"]["arguments"]

                    # Check for cache hit indicator in usage
                    if "usage" in chunk and chunk["usage"] is not None:
                        usage = chunk["usage"]
                        prompt_tokens = usage.get("prompt_tokens", 0)
                        cached_tokens = usage.get("prompt_cache_hit_tokens", 0)
                        if prompt_tokens > 0 and cached_tokens > 0:
                            ratio = cached_tokens / prompt_tokens
                            cache_hit = ratio > 0.5

                # Assemble final tool calls
                final_calls = []
                for idx in sorted(tool_calls_buf.keys()):
                    tc = tool_calls_buf[idx]
                    if tc["id"] and tc["function"]["name"]:
                        final_calls.append(
                            ToolCall(
                                id=tc["id"],
                                name=tc["function"]["name"],
                                arguments=tc["function"]["arguments"],
                            )
                        )

                # Final chunk carries only the accumulated metadata
                # (tool_calls / usage / cache_hit). content & thinking are
                # already streamed incrementally above — re-sending the full
                # buffers here would duplicate the response text.
                yield ModelResponse(
                    content="",
                    tool_calls=final_calls,
                    thinking="",
                    usage=usage,
                    cache_hit=cache_hit,
                )

    async def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.0,
        max_tokens: int = 65536,
    ) -> ModelResponse:
        """Non-streaming completion."""
        api_messages = self._build_messages(messages)
        api_tools = self._build_tools(tools)

        payload = {
            "model": self.model,
            "messages": api_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if api_tools:
            payload["tools"] = api_tools

        # Reasoning effort
        reasoning_effort = getattr(self, '_reasoning_effort', None)
        if reasoning_effort and reasoning_effort != "low":
            payload["reasoning_effort"] = reasoning_effort

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

            choice = data["choices"][0]
            msg = choice["message"]
            usage = data.get("usage", {})

            # Cache hit detection
            cached_tokens = usage.get("prompt_cache_hit_tokens", 0)
            prompt_tokens = usage.get("prompt_tokens", 0)
            cache_hit = (prompt_tokens > 0 and cached_tokens > 0
                         and (cached_tokens / prompt_tokens) > 0.5)

            tool_calls = []
            for tc in msg.get("tool_calls", []):
                tool_calls.append(
                    ToolCall(
                        id=tc["id"],
                        name=tc["function"]["name"],
                        arguments=tc["function"].get("arguments", ""),
                    )
                )

            return ModelResponse(
                content=msg.get("content", ""),
                tool_calls=tool_calls,
                thinking=msg.get("reasoning_content", ""),
                usage=usage,
                cache_hit=cache_hit,
            )