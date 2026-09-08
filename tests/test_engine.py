"""Tests for the engine module."""

import asyncio
import pytest
from zouwucode.engine.cache import PrefixCache, CacheStats
from zouwucode.engine.loop import EngineLoop, TurnLimitExceeded
from zouwucode.engine.providers.base import (
    BaseProvider,
    Message,
    ToolCall,
    ToolResult,
    ModelResponse,
)
from zouwucode.config import ZOUWUCODEConfig


class TestPrefixCache:
    """Tests for the PrefixCache class."""

    def test_freeze_and_append(self):
        cache = PrefixCache(max_prefix_tokens=1000)
        assert not cache.is_frozen

        cache.freeze([{"role": "system", "content": "You are a helpful AI."}])
        assert cache.is_frozen
        assert len(cache.get_prefix()) == 1

        cache.append({"role": "user", "content": "Hello"})
        assert len(cache.get_prefix()) == 2

    def test_append_only_guarantee(self):
        cache = PrefixCache()
        cache.freeze([{"role": "system", "content": "sys"}])
        cache.append({"role": "user", "content": "msg1"})
        cache.append({"role": "assistant", "content": "resp1"})

        prefix = cache.get_prefix()
        assert prefix[0]["role"] == "system"
        assert prefix[1]["role"] == "user"
        assert prefix[2]["role"] == "assistant"

    def test_reset(self):
        cache = PrefixCache()
        cache.freeze([{"role": "system", "content": "sys"}])
        cache.append({"role": "user", "content": "msg"})
        cache.reset()
        assert not cache.is_frozen
        assert len(cache.get_prefix()) == 0

    def test_can_append(self):
        cache = PrefixCache(max_prefix_tokens=10)
        cache.freeze([{"role": "system", "content": "a" * 10}])
        # Rough estimate: 10 chars ≈ 5 tokens, so this should be fine
        assert cache.can_append(5)


class TestCacheStats:
    """Tests for the CacheStats class."""

    def test_initial_state(self):
        stats = CacheStats()
        assert stats.hit_rate == 0.0
        assert stats.total_requests == 0
        assert stats.total_cost == 0.0

    def test_record_turn_hit(self):
        stats = CacheStats()
        stats.record_turn(True, {
            "prompt_tokens": 1000,
            "prompt_cache_hit_tokens": 800,
            "completion_tokens": 200,
        })
        assert stats.total_requests == 1
        assert stats.cache_hits == 1
        assert stats.hit_rate == 1.0
        assert stats.total_prompt_tokens == 1000
        assert stats.cached_prompt_tokens == 800
        assert stats.total_output_tokens == 200

    def test_record_turn_miss(self):
        stats = CacheStats()
        stats.record_turn(False, {
            "prompt_tokens": 1000,
            "prompt_cache_hit_tokens": 0,
            "completion_tokens": 200,
        })
        assert stats.hit_rate == 0.0

    def test_hit_rate_mixed(self):
        stats = CacheStats()
        stats.record_turn(True, {"prompt_tokens": 100, "prompt_cache_hit_tokens": 80, "completion_tokens": 20})
        stats.record_turn(False, {"prompt_tokens": 100, "prompt_cache_hit_tokens": 0, "completion_tokens": 20})
        stats.record_turn(True, {"prompt_tokens": 100, "prompt_cache_hit_tokens": 90, "completion_tokens": 20})
        assert stats.hit_rate == 2 / 3
        assert stats.total_requests == 3


class TestMessage:
    def test_to_dict(self):
        msg = Message("user", "hello")
        d = msg.to_dict()
        assert d["role"] == "user"
        assert d["content"] == "hello"

    def test_to_dict_with_tool_calls(self):
        msg = Message("assistant", "", [{"id": "1", "type": "function"}])
        d = msg.to_dict()
        assert len(d["tool_calls"]) == 1


class TestToolCall:
    def test_to_dict(self):
        tc = ToolCall(id="call_1", name="read", arguments='{"file_path": "test.py"}')
        d = tc.to_dict()
        assert d["id"] == "call_1"
        assert d["function"]["name"] == "read"


class TestToolResult:
    def test_to_dict(self):
        tr = ToolResult(tool_call_id="call_1", content="file content", is_error=False)
        d = tr.to_dict()
        assert d["role"] == "tool"
        assert d["content"] == "file content"


class TestModelResponse:
    def test_defaults(self):
        resp = ModelResponse()
        assert resp.content == ""
        assert resp.tool_calls == []
        assert resp.thinking == ""

    def test_with_data(self):
        tc = ToolCall(id="c1", name="bash", arguments='{"command": "ls"}')
        resp = ModelResponse(
            content="done",
            tool_calls=[tc],
            thinking="let me think...",
            usage={"prompt_tokens": 100},
            cache_hit=True,
        )
        assert resp.content == "done"
        assert len(resp.tool_calls) == 1
        assert resp.thinking == "let me think..."
        assert resp.cache_hit is True


class _StreamingProvider(BaseProvider):
    """Fake provider that yields thinking/content deltas incrementally."""

    def __init__(self, thinking_deltas, content_deltas, with_tool_calls=False):
        super().__init__({"api_key": "sk-test", "model": "fake"})
        self.thinking_deltas = thinking_deltas
        self.content_deltas = content_deltas
        self.with_tool_calls = with_tool_calls

    async def chat_stream(self, messages, tools=None, temperature=0.0,
                          max_tokens=65536, stream_thinking=True):
        for d in self.thinking_deltas:
            yield ModelResponse(content="", thinking=d)
        for d in self.content_deltas:
            yield ModelResponse(content=d, thinking="")
        tc = [ToolCall(id="c1", name="read", arguments='{"file_path": "x.py"}')] if self.with_tool_calls else []
        yield ModelResponse(
            content="", tool_calls=tc, thinking="",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            cache_hit=True,
        )

    async def chat(self, messages, tools=None, temperature=0.0, max_tokens=65536):
        return ModelResponse(content="".join(self.content_deltas))


class TestStreamingThinking:
    """Streaming thinking display — deltas must flow to callbacks and aggregate."""

    @pytest.fixture
    def engine(self):
        config = ZOUWUCODEConfig()
        provider = _StreamingProvider(
            thinking_deltas=["let me ", "analyze ", "the code"],
            content_deltas=["Hello ", "world"],
        )
        loop = EngineLoop(config, provider)
        return loop

    def test_thinking_deltas_reach_callback(self, engine):
        received = []
        engine.on_thinking_delta = received.append

        asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))

        assert received == ["let me ", "analyze ", "the code"]

    def test_thinking_aggregated_in_response(self, engine):
        resp = asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))
        assert resp.thinking == "let me analyze the code"
        assert resp.content == "Hello world"

    def test_content_deltas_reach_callback(self, engine):
        received = []
        engine.on_content_delta = received.append

        asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))

        assert received == ["Hello ", "world"]

    def test_callbacks_cleared_do_not_crash(self, engine):
        # Default hooks are None — must not raise
        resp = asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))
        assert resp.content == "Hello world"

    def test_show_thinking_defaults_on(self):
        config = ZOUWUCODEConfig()
        assert config.show_thinking is True

    def test_show_thinking_toggle(self):
        config = ZOUWUCODEConfig()
        config.show_thinking = False
        assert config.show_thinking is False


class _FakeStreamResp:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200

    def raise_for_status(self):
        pass

    async def aread(self):
        return b""

    def aiter_lines(self):
        class _It:
            def __init__(self, lines):
                self._lines = iter(lines)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self._lines)
                except StopIteration:
                    raise StopAsyncIteration

        return _It(self._lines)


class _FakeStreamCtx:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _FakeStreamResp(self._lines)

    async def __aexit__(self, *a):
        return False


class _FakeAsyncClient:
    def __init__(self, timeout=None):
        pass

    def stream(self, method, url, json=None, headers=None):
        lines = [
            'data: {"choices":[{"delta":{"reasoning_content":"think 1"}}]}',
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            'data: {"choices":[{"delta":{"content":" world"}}]}',
            'data: {"usage":{"prompt_tokens":10,"completion_tokens":5}}',
            'data: [DONE]',
        ]
        return _FakeStreamCtx(lines)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class TestProviderStreamingNoDedup:
    """Providers must stream incrementally — the final chunk must not repeat
    the full content/thinking that was already sent as deltas."""

    def _collect(self, provider):
        chunks = []

        async def _run():
            async for c in provider.chat_stream(messages=[{"role": "user", "content": "hi"}]):
                chunks.append(c)

        asyncio.run(_run())
        return chunks

    def test_deepseek_streams_incrementally(self, monkeypatch):
        from zouwucode.engine.providers import deepseek as ds

        monkeypatch.setattr(ds.httpx, "AsyncClient", _FakeAsyncClient)
        provider = ds.DeepSeekProvider({"api_key": "sk-test", "model": "deepseek-v4-flash"})
        chunks = self._collect(provider)

        contents = "".join(c.content for c in chunks)
        thinkings = "".join(c.thinking for c in chunks)
        assert contents == "Hello world", "内容应恰好一次（增量拼接）"
        assert thinkings == "think 1", "思考应恰好一次（增量拼接）"
        # 最后一个 chunk 只携带元数据，不得重复携带完整文本
        assert chunks[-1].content == ""
        assert chunks[-1].thinking == ""

    def test_openai_streams_incrementally(self, monkeypatch):
        from zouwucode.engine.providers import openai as oa

        monkeypatch.setattr(oa.httpx, "AsyncClient", _FakeAsyncClient)
        provider = oa.OpenAIProvider({"api_key": "sk-test", "model": "gpt-4o",
                                      "base_url": "https://api.openai.com/v1"})
        chunks = self._collect(provider)

        contents = "".join(c.content for c in chunks)
        assert contents == "Hello world", "内容应恰好一次（增量拼接）"
        assert chunks[-1].content == ""


class TestCoordinatorToolDispatch:
    """Agent tool dispatch — JSON-string arguments must be parsed before **."""

    async def _run(self, tool_call):
        from zouwucode.agent.coordinator import AgentCoordinator
        from zouwucode.engine.loop import EngineLoop, TurnContext

        config = ZOUWUCODEConfig()

        class _Registry:
            async def execute(self, name, **kwargs):
                return type("R", (), {"output": f"{name}:{sorted(kwargs)}", "success": True})()

        engine = EngineLoop(config, _StreamingProvider([], []))
        coord = AgentCoordinator(config, engine, _Registry())
        return await coord.execute_tool(tool_call, TurnContext([], [], mode="yolo"))

    def test_string_arguments_parsed(self):
        resp = asyncio.run(self._run(ToolCall(id="t1", name="read",
                                              arguments='{"file_path": "a.py"}')))
        assert resp.is_error is False
        assert resp.content == "read:['file_path']"

    def test_invalid_arguments_return_error(self):
        resp = asyncio.run(self._run(ToolCall(id="t1", name="read",
                                              arguments="not-json")))
        assert resp.is_error is True
        assert resp.content, "应返回具体错误信息"

    def test_empty_arguments_ok(self):
        resp = asyncio.run(self._run(ToolCall(id="t1", name="ls", arguments="")))
        assert resp.is_error is False


class TestLoopSafetyLimits:
    """Engine loop safety limits — infinite tool loops / timeouts must
    terminate with TurnLimitExceeded instead of hanging forever."""

    def _engine(self, provider, **engine_cfg):
        config = ZOUWUCODEConfig()
        for key, value in engine_cfg.items():
            setattr(config.engine, key, value)
        return EngineLoop(config, provider)

    @staticmethod
    def _failing_executor(calls: list):
        async def executor(tool_call, context):
            calls.append(tool_call.name)
            return ToolResult(tool_call_id=tool_call.id,
                              content="boom", is_error=True)
        return executor

    def test_infinite_tool_loop_terminated(self):
        """模型每轮都返回 tool_calls 且工具持续失败 → 必须在轮次上限处终止。"""
        calls: list = []

        class _LoopProvider(BaseProvider):
            async def chat_stream(self, messages, tools=None, temperature=0.0,
                                  max_tokens=65536, stream_thinking=True):
                yield ModelResponse(content="", tool_calls=[
                    ToolCall(id="c1", name="read", arguments='{}'),
                ])
                yield ModelResponse(content="", tool_calls=[], usage={})

            async def chat(self, messages, tools=None, temperature=0.0,
                           max_tokens=65536):
                return ModelResponse(content="")

        engine = self._engine(_LoopProvider({"api_key": "sk-test"}),
                              max_tool_rounds=5,
                              max_consecutive_tool_errors=99)
        engine.set_tool_executor(self._failing_executor(calls))

        with pytest.raises(TurnLimitExceeded, match="max_tool_rounds"):
            asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))

        # 死循环被硬性截断：执行次数 = 轮次上限，而不是无限
        assert len(calls) == 5

    def test_consecutive_tool_errors_abort_early(self):
        """同一工具连续失败达到阈值 → 提前终止，不必耗尽轮次上限。"""
        calls: list = []

        class _LoopProvider(BaseProvider):
            async def chat_stream(self, messages, tools=None, temperature=0.0,
                                  max_tokens=65536, stream_thinking=True):
                yield ModelResponse(content="", tool_calls=[
                    ToolCall(id="c1", name="bash", arguments='{}'),
                ])
                yield ModelResponse(content="", tool_calls=[], usage={})

            async def chat(self, messages, tools=None, temperature=0.0,
                           max_tokens=65536):
                return ModelResponse(content="")

        engine = self._engine(_LoopProvider({"api_key": "sk-test"}),
                              max_tool_rounds=10,
                              max_consecutive_tool_errors=2)
        engine.set_tool_executor(self._failing_executor(calls))

        with pytest.raises(TurnLimitExceeded, match="consecutive tool errors"):
            asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))

        assert len(calls) == 2, "连续失败 2 次即应终止，而非继续重试"

    def test_normal_multi_round_task_converges(self):
        """正常场景：前两轮工具调用成功，第三轮返回最终文本 → 正常完成。"""
        executed: list = []

        class _ConvergingProvider(BaseProvider):
            def __init__(self):
                super().__init__({"api_key": "sk-test"})
                self.round = 0

            async def chat_stream(self, messages, tools=None, temperature=0.0,
                                  max_tokens=65536, stream_thinking=True):
                self.round += 1
                if self.round <= 2:
                    yield ModelResponse(content="", tool_calls=[
                        ToolCall(id=f"c{self.round}", name="read",
                                 arguments='{"file_path": "a.py"}'),
                    ])
                    yield ModelResponse(content="", tool_calls=[], usage={})
                else:
                    yield ModelResponse(content="任务完成")
                    yield ModelResponse(content="", tool_calls=[], usage={})

            async def chat(self, messages, tools=None, temperature=0.0,
                           max_tokens=65536):
                return ModelResponse(content="任务完成")

        async def executor(tool_call, context):
            executed.append(tool_call.name)
            return ToolResult(tool_call_id=tool_call.id,
                              content="ok", is_error=False)

        engine = self._engine(_ConvergingProvider(), max_tool_rounds=5)
        engine.set_tool_executor(executor)

        resp = asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))
        assert resp.content == "任务完成"
        assert executed == ["read", "read"]
        assert engine.mode == "agent"

    def test_hung_llm_request_times_out(self):
        """LLM 流式请求挂起（连接假死）→ 单轮超时终止，不再永久停顿。"""
        class _HungProvider(BaseProvider):
            async def chat_stream(self, messages, tools=None, temperature=0.0,
                                  max_tokens=65536, stream_thinking=True):
                await asyncio.sleep(30)  # 模拟连接假死
                yield ModelResponse(content="never")

            async def chat(self, messages, tools=None, temperature=0.0,
                           max_tokens=65536):
                return ModelResponse(content="never")

        engine = self._engine(_HungProvider({"api_key": "sk-test"}),
                              turn_timeout_seconds=0.2)
        with pytest.raises(TurnLimitExceeded, match="timed out"):
            asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))

    def test_plan_mode_tool_calls_do_not_loop(self):
        """plan 模式：工具调用被跳过，循环立即结束。"""
        class _ToolProvider(BaseProvider):
            async def chat_stream(self, messages, tools=None, temperature=0.0,
                                  max_tokens=65536, stream_thinking=True):
                yield ModelResponse(content="plan answer", tool_calls=[
                    ToolCall(id="c1", name="read", arguments='{}'),
                ])
                yield ModelResponse(content="", tool_calls=[], usage={})

            async def chat(self, messages, tools=None, temperature=0.0,
                           max_tokens=65536):
                return ModelResponse(content="plan answer")

        calls: list = []
        engine = self._engine(_ToolProvider({"api_key": "sk-test"}))
        engine.set_mode("plan")
        engine.set_tool_executor(self._failing_executor(calls))

        resp = asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))
        assert resp.content == "plan answer"
        assert calls == [], "plan 模式不得执行任何工具"