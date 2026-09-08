"""OpenAI-compatible provider for multi-model support."""

import json
from typing import AsyncIterator, Optional

import httpx

from .base import BaseProvider, ModelResponse, ProviderAPIError, ToolCall


class OpenAIProvider(BaseProvider):
    """Generic OpenAI-compatible provider (works with OpenAI, Ollama, etc.)."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.base_url = config.get("base_url", "https://api.openai.com/v1")
        self.model = config.get("model", "gpt-4o")

    async def chat_stream(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.0,
        max_tokens: int = 65536,
        stream_thinking: bool = True,
    ) -> AsyncIterator[ModelResponse]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            # Required for OpenAI to include usage in the final stream chunk
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

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
                    raise RuntimeError(
                        f"OpenAI API {resp.status_code}: {error_body.decode('utf-8', errors='replace')[:500]}"
                    )
                tool_calls_buf = {}
                usage = {}

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

                    if chunk.get("usage") is not None:
                        usage = chunk["usage"]

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue

                    delta = choices[0].get("delta", {})
                    if delta.get("content"):
                        content_delta = delta["content"]
                        # Incremental content stream
                        yield ModelResponse(
                            content=content_delta, thinking="",
                            tool_calls=[], usage={},
                        )

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

                # Final chunk carries only tool_calls/usage. content is already
                # streamed incrementally above — avoid duplicating the response.
                yield ModelResponse(
                    content="",
                    tool_calls=final_calls,
                    thinking="",
                    usage=usage,
                )

    async def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.0,
        max_tokens: int = 65536,
    ) -> ModelResponse:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

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
                usage=usage,
            )