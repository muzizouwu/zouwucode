"""Regression for v1.0.6: concurrency + resilience + streaming (engine).

- independent tool calls in one round now run CONCURRENTLY (they were
  sequential — a round with N tools cost N waits);
- a single hung tool is bounded by a per-tool timeout and returns an error
  result instead of stalling the whole task;
- tool results are still recorded in the model's original order;
- coordinator keeps BOTH stdout and stderr on failure;
- the CLI streams the assistant body (not only thinking).
"""

import asyncio
import time
from pathlib import Path

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.engine.loop import EngineLoop
from zouwucode.engine.providers.base import BaseProvider, ModelResponse, ToolCall

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class _ScriptedProvider(BaseProvider):
    """Emits one round of tool calls, then a plain final answer."""

    def __init__(self, ncalls: int):
        super().__init__({"api_key": "sk-test"})
        self.ncalls = ncalls
        self.round = 0

    async def chat_stream(self, messages, tools=None, temperature=0.0,
                          max_tokens=65536, stream_thinking=True):
        self.round += 1
        if self.round == 1:
            yield ModelResponse(content="", tool_calls=[
                ToolCall(id=f"c{i}", name=f"t{i}", arguments="{}")
                for i in range(self.ncalls)
            ])
        else:
            yield ModelResponse(content="done")

    async def chat(self, messages, tools=None, temperature=0.0, max_tokens=65536):
        return ModelResponse(content="done")


def _engine(provider, mode="yolo", **cfg):
    config = ZOUWUCODEConfig()
    config.sandbox.default_mode = mode
    for k, v in cfg.items():
        setattr(config.engine, k, v)
    eng = EngineLoop(config, provider)
    eng.set_mode(mode)
    return eng


def _run(engine, executor, ncalls):
    engine.set_tool_executor(executor)
    return asyncio.run(engine.run(messages=[{"role": "user", "content": "go"}]))


class TestParallelTools:
    def test_independent_tools_run_concurrently(self):
        N, DELAY = 4, 0.25
        order = []

        async def executor(tc, ctx):
            order.append(("start", tc.name))
            await asyncio.sleep(DELAY)
            order.append(("end", tc.name))
            from zouwucode.engine.providers.base import ToolResult
            return ToolResult(tool_call_id=tc.id, content="ok", is_error=False)

        eng = _engine(_ScriptedProvider(N))
        t0 = time.monotonic()
        _run(eng, executor, N)
        elapsed = time.monotonic() - t0
        # Sequential would be >= N*DELAY; concurrent ~ DELAY.
        assert elapsed < N * DELAY * 0.8, f"elapsed={elapsed:.2f}s looks sequential"
        # results still recorded in the model's original order
        names = [r for r in eng.cache.get_prefix() if r.get("tool_call_id")]
        assert [n["tool_call_id"] for n in names] == [f"c{i}" for i in range(N)]

    def test_agent_mode_stays_sequential(self):
        # concurrent approval prompts would fight over the terminal
        N, DELAY = 4, 0.2
        async def executor(tc, ctx):
            await asyncio.sleep(DELAY)
            from zouwucode.engine.providers.base import ToolResult
            return ToolResult(tool_call_id=tc.id, content="ok", is_error=False)

        eng = _engine(_ScriptedProvider(N), mode="agent")
        t0 = time.monotonic()
        _run(eng, executor, N)
        assert (time.monotonic() - t0) >= N * DELAY * 0.8


class TestPerToolTimeout:
    def test_hung_tool_returns_error_not_stall(self):
        async def executor(tc, ctx):
            await asyncio.sleep(30)  # would hang the task
            from zouwucode.engine.providers.base import ToolResult
            return ToolResult(tool_call_id=tc.id, content="never", is_error=False)

        eng = _engine(_ScriptedProvider(1), turn_timeout_seconds=1.0)
        t0 = time.monotonic()
        resp = _run(eng, executor, 1)
        elapsed = time.monotonic() - t0
        assert elapsed < 5, "task should not wait for the hung tool"
        assert "timed out" in (resp.content or "").lower() or resp.content == "done"
        # the timeout surfaced as a tool result the model saw (in the cache)
        msgs = [m for m in eng.cache.get_prefix() if m.get("tool_call_id")]
        assert msgs and "timed out" in str(msgs[0].get("content", "")).lower()


class TestCoordinatorErrorText:
    def test_keeps_both_streams(self):
        src = _read("zouwucode/agent/coordinator.py")
        assert '"\\n".join(parts)' in src
        assert "content = result.output or \"\"" in src


class TestCliStreaming:
    def test_body_streaming_wired(self):
        src = _read("zouwucode/tui/app.py")
        assert "def _stream_content" in src
        assert src.count("on_content_delta = self._stream_content") == 2
        # guard against double-printing the streamed body
        assert "not self._content_streaming" in src


class TestEngineConcurrencySource:
    def test_gather_used(self):
        src = _read("zouwucode/engine/loop.py")
        assert "asyncio.gather(*[" in src
        assert "def _exec_one_tool" in src
        assert "asyncio.wait_for(" in src
