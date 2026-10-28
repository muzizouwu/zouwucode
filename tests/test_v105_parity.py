"""Regression for v1.0.5: cross-frontend parity, cost accuracy, de-bloat.

Pins the defects the self-audit turned up:
- three different version strings (app banner said 1.0.0 while the package
  was 1.0.4);
- argparse defaults (--mode/--reasoning) were truthy, so config.yaml's
  sandbox.default_mode / reasoning_intensity were silently ignored; --provider
  was a dead flag;
- DeepSeek cost was $0 in 'low' reasoning mode (stream_options popped);
- cost was hardcoded to one model's price regardless of the model;
- the Web UI lagged the CLI/TUI (no memory/goal/decide/context/agents/mode);
- CLI printed no tool-call trace, and Ctrl+C exited instead of interrupting;
- duplicated sub-agent/extension wiring across the two UI apps.
"""

import inspect
import re
from pathlib import Path

import pytest

import zouwucode
from zouwucode import __main__ as zmain

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestVersionConsistency:
    def test_package_version_is_semver(self):
        assert re.match(r"^\d+\.\d+\.\d+$", zouwucode.__version__)

    def test_app_banner_uses_package_version(self):
        from zouwucode.tui.app import ZOUWUCODEApp
        assert ZOUWUCODEApp.APP_VERSION == zouwucode.__version__

    def test_build_script_uses_package_version(self):
        src = _read("build.py")
        assert "from zouwucode import __version__" in src
        assert 'VERSION = "1.0.0"' not in src

    def test_pyproject_matches_package(self):
        assert 'version = "%s"' % zouwucode.__version__ in _read("pyproject.toml")


class TestCliFlagsHonorConfig:
    def test_mode_and_reasoning_default_none(self):
        src = inspect.getsource(zmain)
        assert 'choices=["plan", "agent", "yolo"], default=None' in src
        assert re.search(r'"--reasoning".*?default=None', src, re.S)

    def test_provider_flag_wired(self):
        src = inspect.getsource(zmain)
        assert 'parser.add_argument("--provider", type=str, default=None' in src
        assert "provider=args.provider" in src

    def test_app_provider_kwarg_selects_provider(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from zouwucode.tui.app import ZOUWUCODEApp
        app = ZOUWUCODEApp(provider="openai")
        assert app.config.default_provider == "openai"
        assert type(app._provider).__name__ == "OpenAIProvider"


class TestCostModel:
    """Cost accounting follows the official DeepSeek V4 price sheet.

    Rates are peak rates; DeepSeek bills half outside peak windows, so the
    tests pin is_peak_hour to keep assertions time-independent.
    """

    @staticmethod
    def _pin(monkeypatch, peak: bool):
        import zouwucode.engine.cache as cache
        monkeypatch.setattr(cache, "is_peak_hour", lambda now=None: peak)
        return cache

    def test_price_table_by_model(self, monkeypatch):
        self._pin(monkeypatch, True)
        from zouwucode.engine.cache import CacheStats
        s = CacheStats(model="deepseek-flash")
        s.record_turn(True, {"prompt_tokens": 1_000_000,
                             "prompt_cache_hit_tokens": 0, "completion_tokens": 0})
        assert abs(s.total_cost - 0.30) < 1e-6

    def test_off_peak_is_half_price(self, monkeypatch):
        self._pin(monkeypatch, False)
        from zouwucode.engine.cache import CacheStats
        s = CacheStats(model="deepseek-flash")
        s.record_turn(True, {"prompt_tokens": 1_000_000,
                             "prompt_cache_hit_tokens": 0, "completion_tokens": 0})
        assert abs(s.total_cost - 0.15) < 1e-6

    def test_unknown_model_falls_back(self, monkeypatch):
        self._pin(monkeypatch, True)
        from zouwucode.engine.cache import CacheStats, DEFAULT_PRICING
        s = CacheStats(model="some-local-llm")
        s.record_turn(True, {"prompt_tokens": 1_000_000,
                             "prompt_cache_hit_tokens": 0, "completion_tokens": 0})
        assert abs(s.total_cost - DEFAULT_PRICING[0]) < 1e-6

    def test_expensive_model_costs_more(self, monkeypatch):
        self._pin(monkeypatch, True)
        from zouwucode.engine.cache import CacheStats
        gpt = CacheStats(model="gpt-4o")
        ds = CacheStats(model="deepseek-flash")
        usage = {"prompt_tokens": 500_000, "prompt_cache_hit_tokens": 0,
                 "completion_tokens": 1_000}
        gpt.record_turn(True, usage)
        ds.record_turn(True, usage)
        # Peak: gpt-4o $1.26 vs flash $0.31 → ~4x. Assert a clear margin
        # without hard-coding the exact multiple (prices change over time).
        assert gpt.total_cost > ds.total_cost * 3

    def test_openai_models_do_not_get_off_peak_discount(self, monkeypatch):
        """Only DeepSeek has peak/off-peak billing; OpenAI prices are flat."""
        cache = self._pin(monkeypatch, False)
        s = cache.CacheStats(model="gpt-4o")
        s.record_turn(True, {"prompt_tokens": 1_000_000,
                             "prompt_cache_hit_tokens": 0, "completion_tokens": 0})
        assert abs(s.total_cost - 2.50) < 1e-6

    def test_cached_never_negative(self):
        from zouwucode.engine.cache import CacheStats
        s = CacheStats(model="deepseek-flash")
        s.record_turn(True, {"prompt_tokens": 100, "prompt_cache_hit_tokens": 500,
                             "completion_tokens": 0})
        assert s.total_cost >= 0

    def test_engine_passes_model(self):
        assert 'model=getattr(provider, "model", "")' in _read("zouwucode/engine/loop.py")


class TestProviderCorrectness:
    def test_deepseek_always_requests_usage(self):
        src = _read("zouwucode/engine/providers/deepseek.py")
        assert 'payload.pop("stream_options"' not in src
        assert '"stream_options"] = {"include_usage": True}' in src

    def test_openai_raises_retryable_error(self):
        src = _read("zouwucode/engine/providers/openai.py")
        assert "raise ProviderAPIError(" in src
        assert "status_code=resp.status_code" in src


class TestWebParity:
    @classmethod
    def setup_class(cls):
        cls.src = _read("zouwucode/webui/server.py")
        cls.js = _read("zouwucode/webui/static/app.js")

    @pytest.mark.parametrize("route", ["/api/memory", "/api/goal", "/api/decide",
                                       "/api/context", "/api/compress", "/api/agents"])
    def test_endpoint_present(self, route):
        assert '"' + route + '"' in self.src

    @pytest.mark.parametrize("cmd", ["/memory", "/decide", "/context",
                                     "/compress", "/agents"])
    def test_js_command_present(self, cmd):
        assert ('{ cmd: "' + cmd + '"') in self.js

    def test_mode_commands(self):
        for mode in ("/plan", "/agent", "/yolo"):
            assert ('{ cmd: "' + mode + '"') in self.js

    def test_hello_agents_is_real_dispatch(self):
        assert '"/api/agents"' in self.js, "前端应调用 /api/agents"
        assert '"/api/agents"' in self.src

    def test_subagent_manager_wired(self):
        assert "subagent_manager=None," in self.src
        assert "self.subagent_manager = subagent_manager" in self.src

    def test_status_reports_config_budget(self):
        assert '"max_context_tokens": 1048576,' not in self.src

    def test_app_passes_subagent_manager(self):
        assert "subagent_manager=self.subagent_manager" in _read("zouwucode/tui/app.py")


class TestCliInteractivity:
    def test_tool_event_wired(self):
        src = _read("zouwucode/tui/app.py")
        assert "self.engine.on_tool_event = self._on_tool_event" in src
        assert "def _on_tool_event" in src

    def test_ctrl_c_interrupts_running_task(self):
        src = _read("zouwucode/tui/app.py")
        sig = src[src.index("def _signal_handler"):]
        assert 'request_interrupt(reason="CLI Ctrl+C")' in sig[:600]
        assert "is_running" in sig[:600]


class TestSharedWiring:
    def test_factory_exists(self):
        assert "def create_subagents_and_extensions" in _read("zouwucode/runtime.py")

    def test_both_frontends_use_factory(self):
        for rel in ("zouwucode/tui/app.py", "zouwucode/tui/textual_app.py"):
            src = _read(rel)
            assert "create_subagents_and_extensions(" in src
            assert "self.subagent_manager = SubAgentManager(" not in src

    def test_unused_imports_removed(self):
        assert "\nimport uuid\n" not in _read("zouwucode/webui/server.py")
        assert "from ..errors import describe_exc" not in _read("zouwucode/dev/pipeline.py")
        assert "\nimport json\n" not in _read("zouwucode/tools/agent_tools.py")
