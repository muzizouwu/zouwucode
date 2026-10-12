"""Regression for v1.1.0: repo-map (Aider-style) + cost transparency (/usage).

- repo_map: a ranked, signature-only codebase overview, exposed as a tool and
  optionally injected into the frozen system prompt (off by default).
- /usage: cost/usage transparency across all three frontends, including the
  cache savings DeepSeek's cache makes possible.
"""

import asyncio
from pathlib import Path

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.repo_map import build_repo_map, _extract_defs, repo_map_enabled
from zouwucode.engine.cache import CacheStats

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestRepoMapBuilder:
    def _proj(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "core.py").write_text(
            "class Engine:\n    pass\n\ndef run(x):\n    return x\n", encoding="utf-8")
        (tmp_path / "pkg" / "util.py").write_text(
            "def util(x):  # used by core\n    return x\n", encoding="utf-8")
        (tmp_path / "main.py").write_text(
            "from pkg.core import Engine, run\nfrom pkg.util import util\n", encoding="utf-8")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "junk.js").write_text("x", encoding="utf-8")
        return tmp_path

    def test_extracts_signatures(self, tmp_path):
        p = self._proj(tmp_path) / "pkg" / "core.py"
        defs = _extract_defs(p)
        assert "Engine" in defs and "run" in defs

    def test_map_lists_files_and_defs(self, tmp_path):
        m = build_repo_map(self._proj(tmp_path), max_tokens=2000)
        assert "pkg/core.py" in m and "Engine" in m
        assert "main.py" in m

    def test_ignores_vendor_dirs(self, tmp_path):
        m = build_repo_map(self._proj(tmp_path), max_tokens=4000)
        assert "node_modules" not in m

    def test_respects_token_budget(self, tmp_path):
        # a tiny budget must not blow up
        m = build_repo_map(self._proj(tmp_path), max_tokens=1)
        assert len(m) < 400 or m == ""

    def test_referenced_file_ranks_first(self, tmp_path):
        m = build_repo_map(self._proj(tmp_path), max_tokens=4000)
        # main.py imports pkg.core/pkg.util, so they should outrank a leaf
        assert m.index("core.py") < m.index("main.py")


class TestRepoMapTool:
    def test_tool_registered_and_callable(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from zouwucode.runtime import create_builtin_tools
        tools = create_builtin_tools(None, ZOUWUCODEConfig())
        names = [t.get_name() for t in tools]
        assert "repo_map" in names
        tool = [t for t in tools if t.get_name() == "repo_map"][0]
        (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
        r = asyncio.run(tool.execute(path="."))
        assert r.success and "a.py" in r.output

    def test_config_toggle(self):
        cfg = ZOUWUCODEConfig()
        assert repo_map_enabled(cfg) is False       # opt-in (costs tokens)
        cfg.context.repo_map_enabled = True
        assert repo_map_enabled(cfg) is True


class TestUsageSummary:
    def _engine(self):
        from zouwucode.tui.app import ZOUWUCODEApp
        return ZOUWUCODEApp().engine

    def test_usage_summary_fields(self):
        eng = self._engine()
        eng.stats = CacheStats(model="deepseek-v4-flash")
        eng.stats.record_turn(True, {"prompt_tokens": 1_000_000,
                                     "prompt_cache_hit_tokens": 800_000,
                                     "completion_tokens": 1_000})
        u = eng.get_usage_summary()
        assert u["prompt_tokens"] == 1_000_000
        assert u["cached_prompt_tokens"] == 800_000
        assert u["spent_usd"] >= 0
        assert u["cache_saved_usd"] > 0          # 800k cached tokens saved money
        assert u["would_have_cost_usd"] >= u["spent_usd"]
        assert u["model"] == "deepseek-v4-flash"


class TestUsageWiring:
    def test_cli_and_tui_have_usage(self):
        assert 'command == "/usage"' in _read("zouwucode/tui/app.py")
        assert 'command == "/usage"' in _read("zouwucode/tui/textual_app.py")

    def test_web_endpoint_and_command(self):
        web = _read("zouwucode/webui/server.py")
        assert '"/api/usage"' in web
        assert "cmd === '/usage'" in web

    def test_repo_map_injectable_into_prompt(self):
        for rel in ("zouwucode/tui/app.py", "zouwucode/tui/textual_app.py"):
            assert "build_repo_map(" in _read(rel)
