"""Tests for production resilience: LLM retry/backoff and file logging."""

import asyncio
import logging

import httpx
import pytest

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.engine.loop import EngineLoop
from zouwucode.engine.providers.base import (
    BaseProvider,
    ModelResponse,
    ProviderAPIError,
)
from zouwucode.logging_setup import setup_logging


class _FlakyProvider(BaseProvider):
    """Fails `fail_times` times with a transient error, then succeeds."""

    def __init__(self, error, fail_times: int):
        super().__init__({"api_key": "sk-test"})
        self.error = error
        self.fail_times = fail_times
        self.attempts = 0

    async def chat_stream(self, messages, tools=None, temperature=0.0,
                          max_tokens=65536, stream_thinking=True):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise self.error
        yield ModelResponse(content="recovered")
        yield ModelResponse(content="", tool_calls=[], usage={})

    async def chat(self, messages, tools=None, temperature=0.0,
                   max_tokens=65536):
        return ModelResponse(content="recovered")


def _engine(provider, **cfg):
    config = ZOUWUCODEConfig()
    for key, value in cfg.items():
        setattr(config.engine, key, value)
    # 测试不等待真实退避
    config.engine.retry_base_delay_seconds = 0.01
    return EngineLoop(config, provider)


class TestLLMRetry:
    """瞬时故障重试 / 永久错误快速失败。"""

    def test_transient_429_retries_then_succeeds(self):
        provider = _FlakyProvider(
            ProviderAPIError("rate limited", status_code=429), fail_times=1
        )
        engine = _engine(provider, max_llm_retries=2)
        resp = asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))
        assert resp.content == "recovered"
        assert provider.attempts == 2

    def test_transient_5xx_retries(self):
        provider = _FlakyProvider(
            ProviderAPIError("server error", status_code=503), fail_times=2
        )
        engine = _engine(provider, max_llm_retries=2)
        resp = asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))
        assert resp.content == "recovered"
        assert provider.attempts == 3

    def test_transport_error_retries(self):
        provider = _FlakyProvider(
            httpx.ConnectError("connection refused"), fail_times=1
        )
        engine = _engine(provider, max_llm_retries=2)
        resp = asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))
        assert resp.content == "recovered"
        assert provider.attempts == 2

    def test_auth_error_401_not_retried(self):
        """认证错误（401）是永久错误 — 立即失败，不浪费重试。"""
        provider = _FlakyProvider(
            ProviderAPIError("invalid key", status_code=401), fail_times=5
        )
        engine = _engine(provider, max_llm_retries=2)
        with pytest.raises(ProviderAPIError, match="invalid key"):
            asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))
        assert provider.attempts == 1

    def test_bad_request_400_not_retried(self):
        provider = _FlakyProvider(
            ProviderAPIError("bad params", status_code=400), fail_times=5
        )
        engine = _engine(provider, max_llm_retries=2)
        with pytest.raises(ProviderAPIError):
            asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))
        assert provider.attempts == 1

    def test_retries_exhausted_raises(self):
        provider = _FlakyProvider(
            ProviderAPIError("always down", status_code=500), fail_times=99
        )
        engine = _engine(provider, max_llm_retries=2)
        with pytest.raises(ProviderAPIError, match="always down"):
            asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))
        assert provider.attempts == 3  # 1 次初始 + 2 次重试

    def test_retry_disabled_by_config(self):
        provider = _FlakyProvider(
            ProviderAPIError("down", status_code=500), fail_times=99
        )
        engine = _engine(provider, max_llm_retries=0)
        with pytest.raises(ProviderAPIError):
            asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))
        assert provider.attempts == 1

    def test_no_retry_after_stream_started(self):
        """已流出内容后失败不得重试 — 重放会重复渲染 delta。"""
        class _MidStreamFailProvider(BaseProvider):
            def __init__(self):
                super().__init__({"api_key": "sk-test"})
                self.attempts = 0

            async def chat_stream(self, messages, tools=None, temperature=0.0,
                                  max_tokens=65536, stream_thinking=True):
                self.attempts += 1
                yield ModelResponse(content="partial")
                raise ProviderAPIError("stream broke", status_code=500)

            async def chat(self, messages, tools=None, temperature=0.0,
                           max_tokens=65536):
                return ModelResponse(content="")

        provider = _MidStreamFailProvider()
        engine = _engine(provider, max_llm_retries=3)
        with pytest.raises(ProviderAPIError, match="stream broke"):
            asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))
        assert provider.attempts == 1, "流已开始后不应重试"


class TestFileLogging:
    """日志落盘 — 轮转文件真实写入。"""

    def test_setup_logging_creates_file(self, tmp_path, monkeypatch):
        import zouwucode.logging_setup as ls
        monkeypatch.setattr(ls, "_configured", False)  # 重置幂等标志

        log_file = setup_logging(tmp_path, "info")
        assert log_file.exists()
        assert log_file.parent.name == "logs"

        logging.getLogger("zouwucode.engine").info("hello-from-test")
        # 强制刷新 handler
        for h in logging.getLogger("zouwucode").handlers:
            h.flush()
        content = log_file.read_text(encoding="utf-8")
        assert "hello-from-test" in content

    def test_setup_logging_idempotent(self, tmp_path, monkeypatch):
        import zouwucode.logging_setup as ls
        monkeypatch.setattr(ls, "_configured", False)

        def _rotating_count():
            return len([h for h in logging.getLogger("zouwucode").handlers
                        if type(h).__name__ == "RotatingFileHandler"])

        before = _rotating_count()
        f1 = setup_logging(tmp_path, "info")
        f2 = setup_logging(tmp_path, "debug")  # 第二次调用不重复加 handler
        assert f1 == f2
        assert _rotating_count() == before + 1

    def test_engine_task_logging(self, tmp_path, monkeypatch):
        """引擎任务执行过程应写入日志文件。"""
        import zouwucode.logging_setup as ls
        monkeypatch.setattr(ls, "_configured", False)
        log_file = setup_logging(tmp_path, "info")

        class _QuickProvider(BaseProvider):
            async def chat_stream(self, messages, tools=None, temperature=0.0,
                                  max_tokens=65536, stream_thinking=True):
                yield ModelResponse(content="ok")
                yield ModelResponse(content="", tool_calls=[], usage={})

            async def chat(self, messages, tools=None, temperature=0.0,
                           max_tokens=65536):
                return ModelResponse(content="ok")

        engine = _engine(_QuickProvider({"api_key": "sk-test"}))
        asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))
        for h in logging.getLogger("zouwucode").handlers:
            h.flush()
        content = log_file.read_text(encoding="utf-8")
        assert "Task start" in content
        assert "Round 1" in content
        assert "Task end" in content
