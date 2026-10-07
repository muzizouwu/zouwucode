"""Tests for stuck detection — the "tools succeed but model spins" breaker."""

import asyncio

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.engine.loop import EngineLoop, TurnLimitExceeded
from zouwucode.engine.providers.base import BaseProvider, ModelResponse, ToolCall


def _repeating_provider(stop_after: int = 0):
    """Provider that always re-issues the identical tool call.

    If stop_after > 0, switches to a final text answer after that many
    tool rounds (models recovering after the nudge).
    """
    class _P(BaseProvider):
        def __init__(self):
            super().__init__({"api_key": "sk-test"})
            self.round = 0

        async def chat_stream(self, messages, tools=None, temperature=0.0,
                              max_tokens=65536, stream_thinking=True):
            self.round += 1
            if stop_after and self.round > stop_after:
                yield ModelResponse(content="换了思路，任务完成")
                yield ModelResponse(content="", tool_calls=[], usage={})
            else:
                yield ModelResponse(content="", tool_calls=[
                    ToolCall(id=f"c{self.round}", name="read",
                             arguments='{"file_path": "same.py"}'),
                ])
                yield ModelResponse(content="", tool_calls=[], usage={})

        async def chat(self, messages, tools=None, temperature=0.0,
                       max_tokens=65536):
            return ModelResponse(content="")
    return _P()


def _engine(provider, **cfg):
    config = ZOUWUCODEConfig()
    for k, v in cfg.items():
        setattr(config.engine, k, v)
    engine = EngineLoop(config, provider)
    engine.set_mode("yolo")

    async def executor(tool_call, context):
        # identical result every time → fingerprint repeats
        from zouwucode.engine.providers.base import ToolResult
        return ToolResult(tool_call_id=tool_call.id, content="file body",
                          is_error=False)
    engine.set_tool_executor(executor)
    return engine


class TestStuckDetection:
    def test_nudge_then_abort(self):
        """Identical repeats: nudge once, then abort on recurrence."""
        provider = _repeating_provider()
        engine = _engine(provider, max_tool_rounds=20,
                         stuck_detection_enabled=True,
                         stuck_window=10, stuck_threshold=3,
                         max_consecutive_tool_errors=999)
        try:
            asyncio.run(engine.run(
                messages=[{"role": "user", "content": "hi"}]))
            assert False, "expected TurnLimitExceeded"
        except TurnLimitExceeded as exc:
            assert "Stuck" in str(exc)
        # the nudge was injected into the prefix as a user message
        prefix = engine.cache.get_prefix()
        nudges = [m for m in prefix
                  if m.get("role") == "user" and "Stuck check" in str(m.get("content", ""))]
        assert len(nudges) == 1

    def test_recovery_after_nudge(self):
        """Model changes strategy after the nudge → task completes fine."""
        provider = _repeating_provider(stop_after=3)
        engine = _engine(provider, max_tool_rounds=20,
                         stuck_detection_enabled=True,
                         stuck_threshold=3,
                         max_consecutive_tool_errors=999)
        resp = asyncio.run(engine.run(
            messages=[{"role": "user", "content": "hi"}]))
        assert "任务完成" in resp.content

    def test_disabled_falls_back_to_round_cap(self):
        """With detection off, the old max_tool_rounds breaker still fires."""
        provider = _repeating_provider()
        engine = _engine(provider, max_tool_rounds=4,
                         stuck_detection_enabled=False,
                         max_consecutive_tool_errors=999)
        try:
            asyncio.run(engine.run(
                messages=[{"role": "user", "content": "hi"}]))
            assert False
        except TurnLimitExceeded as exc:
            assert "max_tool_rounds" in str(exc)

    def test_varied_actions_never_flagged(self):
        """Different arguments each round → no stuck verdict."""
        class _Varied(BaseProvider):
            def __init__(self):
                super().__init__({"api_key": "sk-test"})
                self.round = 0

            async def chat_stream(self, messages, tools=None, temperature=0.0,
                                  max_tokens=65536, stream_thinking=True):
                self.round += 1
                if self.round > 6:
                    yield ModelResponse(content="完成")
                    yield ModelResponse(content="", tool_calls=[], usage={})
                    return
                yield ModelResponse(content="", tool_calls=[
                    ToolCall(id=f"c{self.round}", name="read",
                             arguments=f'{{"file_path": "f{self.round}.py"}}'),
                ])
                yield ModelResponse(content="", tool_calls=[], usage={})

            async def chat(self, messages, tools=None, temperature=0.0,
                           max_tokens=65536):
                return ModelResponse(content="完成")

        engine = _engine(_Varied(), max_tool_rounds=20,
                         stuck_detection_enabled=True, stuck_threshold=3,
                         max_consecutive_tool_errors=999)
        resp = asyncio.run(engine.run(
            messages=[{"role": "user", "content": "hi"}]))
        assert resp.content == "完成"
