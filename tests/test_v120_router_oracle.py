"""Regression for v1.2.0: model routing + read-only oracle review.

- RouterConfig is opt-in (off by default).
- create_provider_for builds ANY named provider, not just the default.
- ProviderRouter selects worker / oracle providers from config, caching them.
- OracleTool consults the oracle provider with a read-only prompt and reports
  its answer; provider errors become tool errors (never a crash).
- Wiring: create_subagents_and_extensions returns a router and registers the
  `oracle` tool only when configured; all three frontends expose /oracle.
"""

import asyncio
from pathlib import Path

from zouwucode.config import ZOUWUCODEConfig, ProviderConfig
from zouwucode.runtime import (create_provider_for, ProviderRouter,
                               create_subagents_and_extensions)
from zouwucode.engine.providers.base import BaseProvider, ModelResponse
from zouwucode.tools.oracle_tool import OracleTool, ORACLE_SYSTEM

ROOT = Path(__file__).resolve().parent.parent


class _FakeProvider(BaseProvider):
    """Records what it was asked and returns a canned answer."""

    def __init__(self, model="fake-oracle", answer="APPROVE - looks good"):
        super().__init__({"model": model, "api_key": "x"})
        self._answer = answer
        self.seen = None
        self.chat_calls = 0

    async def chat_stream(self, messages, tools=None, temperature=0.0,
                          max_tokens=65536, stream_thinking=True):
        yield ModelResponse(content=self._answer)

    async def chat(self, messages, tools=None, temperature=0.0, max_tokens=65536):
        self.chat_calls += 1
        self.seen = messages
        return ModelResponse(content=self._answer)


class TestRouterConfig:
    def test_opt_in_defaults(self):
        r = ZOUWUCODEConfig().router
        assert r.enabled is False
        assert r.oracle_provider == "" and r.worker_provider == ""

    def test_provider_for_unknown_key_infers_type(self):
        cfg = ZOUWUCODEConfig()
        p = create_provider_for(cfg, "openai_alt")
        assert p.api_type == "openai"
        assert "openai_alt" in cfg.providers


class TestProviderRouter:
    def test_oracle_none_when_disabled(self):
        cfg = ZOUWUCODEConfig()
        cfg.providers["smart"] = ProviderConfig(model="deepseek-reasoner",
                                                api_type="deepseek")
        assert ProviderRouter(cfg).oracle_provider(None) is None

    def test_oracle_selected_when_enabled(self):
        cfg = ZOUWUCODEConfig()
        cfg.router.enabled = True
        cfg.router.oracle_provider = "smart"
        cfg.providers["smart"] = ProviderConfig(model="deepseek-reasoner",
                                                api_type="deepseek")
        router = ProviderRouter(cfg)
        p = router.oracle_provider(None)
        assert p is not None and p.model == "deepseek-reasoner"
        assert router.oracle_provider(None) is p          # cached

    def test_worker_falls_back_to_primary(self):
        cfg = ZOUWUCODEConfig()
        cfg.router.enabled = True                          # no worker key
        primary = _FakeProvider()
        assert ProviderRouter(cfg).worker_provider(primary) is primary

    def test_worker_override(self):
        cfg = ZOUWUCODEConfig()
        cfg.router.enabled = True
        cfg.router.worker_provider = "cheap"
        cfg.providers["cheap"] = ProviderConfig(model="deepseek-v4-flash",
                                                api_type="deepseek")
        primary = _FakeProvider()
        w = ProviderRouter(cfg).worker_provider(primary)
        assert w is not primary and w.model == "deepseek-v4-flash"


class TestOracleTool:
    def test_returns_provider_answer(self):
        fake = _FakeProvider(answer="APPROVE - ship it")
        tool = OracleTool(fake)
        r = asyncio.run(tool.execute(question="review this", context="diff --git ..."))
        assert r.success and "APPROVE - ship it" in r.output
        assert "fake-oracle" in r.output
        assert "NO tools" in ORACLE_SYSTEM               # read-only by prompt
        assert fake.seen[0]["role"] == "system"

    def test_missing_question_is_error(self):
        r = asyncio.run(OracleTool(_FakeProvider()).execute(question="   "))
        assert not r.success and "question" in (r.error or "")

    def test_provider_error_becomes_tool_error(self):
        class Boom(_FakeProvider):
            async def chat(self, *a, **k):
                raise RuntimeError("rate limited")

        r = asyncio.run(OracleTool(Boom()).execute(question="x"))
        assert not r.success and "rate limited" in (r.error or "")


class TestWiring:
    def _stack(self, enabled, oracle_key=""):
        from zouwucode.engine.loop import EngineLoop
        from zouwucode.tools.registry import ToolRegistry
        from zouwucode.sandbox.permission import PermissionManager
        from zouwucode.agent.coordinator import AgentCoordinator

        cfg = ZOUWUCODEConfig()
        cfg.router.enabled = enabled
        cfg.router.oracle_provider = oracle_key
        if oracle_key:
            cfg.providers[oracle_key] = ProviderConfig(model="deepseek-reasoner",
                                                       api_type="deepseek")
        provider = _FakeProvider()
        engine = EngineLoop(cfg, provider)
        sandbox = PermissionManager(cfg.sandbox)
        tools = ToolRegistry()
        coord = AgentCoordinator(cfg, engine, tools)
        engine.set_tool_executor(coord.execute_tool)
        return cfg, provider, engine, coord, tools

    def test_returns_router_third(self):
        cfg, provider, engine, coord, tools = self._stack(True, "smart")
        out = create_subagents_and_extensions(cfg, provider, coord, engine, tools)
        assert len(out) == 3
        _mgr, _host, router = out
        assert isinstance(router, ProviderRouter)

    def test_oracle_tool_registered_when_configured(self):
        cfg, provider, engine, coord, tools = self._stack(True, "smart")
        create_subagents_and_extensions(cfg, provider, coord, engine, tools)
        assert "oracle" in tools.get_names()

    def test_oracle_absent_when_disabled(self):
        cfg, provider, engine, coord, tools = self._stack(False)
        create_subagents_and_extensions(cfg, provider, coord, engine, tools)
        assert "oracle" not in tools.get_names()

    def test_three_frontends_expose_oracle(self):
        for rel in ("zouwucode/tui/app.py", "zouwucode/tui/textual_app.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            assert 'command == "/oracle"' in src, rel
        web = (ROOT / "zouwucode/webui/server.py").read_text(encoding="utf-8")
        assert '"/api/oracle"' in web
        assert "cmd.startsWith('/oracle')" in web
