"""Regression tests for the v1.8.0 upgrade pass (2026-10 external audit).

Pinned defects / new contracts:
1. DeepSeek V4 enables thinking BY DEFAULT — `low` must explicitly send
   {"thinking": {"type": "disabled"}}; merely omitting reasoning_effort left
   the model thinking at high/max while the UI claimed "low".
2. Thinking mode ignores temperature/top_p, so temperature must not be sent
   when thinking is enabled.
3. DeepSeek requires reasoning_content of a tool-calling assistant turn to be
   fed back with the tool results; we previously dropped it.
4. Pricing was badly stale (cache-hit ~5x high, output ~2-4x low) and lacked
   peak/off-peak billing.
5. Default model name moved to `deepseek-flash` (V4 names are legacy aliases).
6. AGENTS.md (cross-tool standard) is now loaded alongside .zouwucode/rules.md.
7. write/edit now return a real unified diff instead of a byte/line count.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.engine.cache import CacheStats, is_peak_hour
from zouwucode.engine.loop import EngineLoop
from zouwucode.engine.providers.base import (
    BaseProvider,
    ModelResponse,
    ToolCall,
    ToolResult,
)
from zouwucode.skills.manager import RuleLoader
from zouwucode.tools.file_tools import EditTool, WriteTool


# ── 1/2. DeepSeek reasoning-mode payload ─────────────────────────────────────

class TestReasoningPayload:
    def _payload(self, level):
        from zouwucode.engine.providers.deepseek import DeepSeekProvider
        p = DeepSeekProvider({"api_key": "sk-test", "model": "deepseek-flash"})
        p.set_reasoning_effort(level)
        return p._build_payload(
            [{"role": "user", "content": "hi"}], None, 0.0, 100)

    def test_low_disables_thinking(self):
        payload = self._payload("low")
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["temperature"] == 0.0
        assert "reasoning_effort" not in payload

    def test_medium_enables_thinking_high_without_temperature(self):
        payload = self._payload("medium")
        assert payload["thinking"] == {"type": "enabled"}
        assert payload["reasoning_effort"] == "high"
        # Thinking mode ignores temperature — sending it is misleading.
        assert "temperature" not in payload

    def test_max_enables_thinking_max(self):
        payload = self._payload("max")
        assert payload["thinking"] == {"type": "enabled"}
        assert payload["reasoning_effort"] == "max"
        assert "temperature" not in payload

    def test_unknown_level_falls_back_to_medium(self):
        payload = self._payload("bogus")
        assert payload["thinking"] == {"type": "enabled"}
        assert payload["reasoning_effort"] == "high"


# ── 3. reasoning_content is fed back after a tool round ─────────────────────

class _ThinkToolThenDone(BaseProvider):
    def __init__(self):
        super().__init__({"api_key": "sk-test"})
        self.turn = 0

    async def chat_stream(self, messages, tools=None, temperature=0.0,
                          max_tokens=65536, stream_thinking=True):
        self.turn += 1
        if self.turn == 1:
            yield ModelResponse(content="", thinking="I should read the file")
            yield ModelResponse(
                content="", thinking="",
                tool_calls=[ToolCall(id="c1", name="read", arguments="{}")])
        else:
            yield ModelResponse(content="done", thinking="", tool_calls=[])

    async def chat(self, messages, tools=None, temperature=0.0,
                   max_tokens=65536):
        return ModelResponse(content="done")


class TestReasoningContentFeedBack:
    def test_tool_turn_carries_reasoning_content(self):
        async def executor(tool_call, context):
            return ToolResult(tool_call_id=tool_call.id, content="ok",
                              is_error=False)

        engine = EngineLoop(ZOUWUCODEConfig(), _ThinkToolThenDone())
        engine.set_tool_executor(executor)
        asyncio.run(engine.run([{"role": "user", "content": "hi"}]))

        assistants = [m for m in engine.cache.get_prefix()
                      if m.get("role") == "assistant"]
        assert assistants[0]["reasoning_content"] == "I should read the file"
        # The final turn has no tool calls, so its reasoning is not resent.
        assert "reasoning_content" not in assistants[1]


# ── 4. Official DeepSeek V4 pricing (peak / off-peak) ───────────────────────

class TestPricing:
    def test_flash_peak(self, monkeypatch):
        import zouwucode.engine.cache as cache
        monkeypatch.setattr(cache, "is_peak_hour", lambda now=None: True)
        assert CacheStats(model="deepseek-flash")._pricing() == (0.30, 0.006, 1.20)

    def test_flash_off_peak_is_half(self, monkeypatch):
        import zouwucode.engine.cache as cache
        monkeypatch.setattr(cache, "is_peak_hour", lambda now=None: False)
        assert CacheStats(model="deepseek-flash")._pricing() == (0.15, 0.003, 0.60)

    def test_pro_peak(self, monkeypatch):
        import zouwucode.engine.cache as cache
        monkeypatch.setattr(cache, "is_peak_hour", lambda now=None: True)
        assert CacheStats(model="deepseek-v4-pro")._pricing() == (1.32, 0.044, 3.96)

    def test_legacy_v4_flash_alias_bills_as_flash(self, monkeypatch):
        import zouwucode.engine.cache as cache
        monkeypatch.setattr(cache, "is_peak_hour", lambda now=None: True)
        assert CacheStats(model="deepseek-v4-flash")._pricing() == (0.30, 0.006, 1.20)

    def test_unknown_model_falls_back_to_flash(self, monkeypatch):
        import zouwucode.engine.cache as cache
        monkeypatch.setattr(cache, "is_peak_hour", lambda now=None: True)
        assert CacheStats(model="totally-unknown")._pricing() == (0.30, 0.006, 1.20)

    def test_peak_windows(self):
        # Peak: 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri.
        assert is_peak_hour(datetime(2026, 10, 26, 2, 0, tzinfo=timezone.utc))
        assert is_peak_hour(datetime(2026, 10, 26, 7, 0, tzinfo=timezone.utc))
        assert not is_peak_hour(datetime(2026, 10, 26, 5, 0, tzinfo=timezone.utc))
        assert not is_peak_hour(datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc))
        # 2026-10-31 is Saturday -> off-peak all day
        assert not is_peak_hour(datetime(2026, 10, 31, 2, 0, tzinfo=timezone.utc))


# ── 5. Default model name ───────────────────────────────────────────────────

class TestDefaultModel:
    def test_provider_default(self):
        from zouwucode.engine.providers.deepseek import DeepSeekProvider
        assert DeepSeekProvider({"api_key": "sk-test"}).model == "deepseek-flash"

    def test_bootstrap_default(self):
        from zouwucode.runtime import ensure_provider_config
        cfg = ZOUWUCODEConfig()
        assert ensure_provider_config(cfg, "deepseek").model == "deepseek-flash"


# ── 6. AGENTS.md ────────────────────────────────────────────────────────────

class TestAgentsMd:
    def test_agents_md_loaded(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text(
            "# Project\nUse pytest.\n", encoding="utf-8")
        loader = RuleLoader(tmp_path)
        assert loader.exists()
        block = loader.get_context_block()
        assert "<agents_md>" in block and "Use pytest" in block

    def test_rules_and_agents_coexist(self, tmp_path):
        (tmp_path / ".zouwucode").mkdir()
        (tmp_path / ".zouwucode" / "rules.md").write_text(
            "Rule A", encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text("Rule B", encoding="utf-8")
        block = RuleLoader(tmp_path).get_context_block()
        assert "<project_rules>" in block and "Rule A" in block
        assert "<agents_md>" in block and "Rule B" in block

    def test_agents_md_is_size_capped(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("x" * 100_000, encoding="utf-8")
        assert len(RuleLoader(tmp_path).load()) <= 32_768

    def test_missing_files_yield_empty_block(self, tmp_path):
        assert RuleLoader(tmp_path).get_context_block() == ""


# ── 7. write / edit return a unified diff ───────────────────────────────────

class TestFileToolDiff:
    def test_write_existing_file_shows_diff(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("old line\n", encoding="utf-8")
        res = asyncio.run(WriteTool().execute(str(f), "new line\n"))
        assert res.success
        assert "-old line" in res.output and "+new line" in res.output

    def test_write_new_file_previews_content(self, tmp_path):
        f = tmp_path / "new.txt"
        res = asyncio.run(WriteTool().execute(str(f), "alpha\nbeta\n"))
        assert res.success
        assert "(new file, 2 lines)" in res.output
        assert "+ alpha" in res.output

    def test_write_identical_content_reports_no_change(self, tmp_path):
        f = tmp_path / "same.txt"
        f.write_bytes(b"same\r\n")
        res = asyncio.run(WriteTool().execute(str(f), "same\n"))
        assert res.success
        assert "no change" in res.output
        assert f.read_bytes() == b"same\r\n", "idempotent write must not alter bytes"

    def test_diff_is_truncated(self, tmp_path):
        f = tmp_path / "big.txt"
        old = "\n".join(f"line {i}" for i in range(2000))
        new = "\n".join(f"changed {i}" for i in range(2000))
        f.write_text(old, encoding="utf-8")
        res = asyncio.run(WriteTool().execute(str(f), new))
        assert "diff truncated" in res.output
        assert len(res.output) < 10_000

    def test_edit_shows_diff(self, tmp_path):
        f = tmp_path / "b.py"
        f.write_text("def f():\n    return 1\n", encoding="utf-8")
        res = asyncio.run(EditTool().execute(
            str(f), "return 1", "return 2"))
        assert res.success
        assert "-    return 1" in res.output and "+    return 2" in res.output

    def test_edit_matches_lf_old_string_in_crlf_file(self, tmp_path):
        """Models write \\n; Windows files have \\r\\n — matching must still work."""
        f = tmp_path / "win.txt"
        f.write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")
        res = asyncio.run(EditTool().execute(
            str(f), "alpha\nbeta", "alpha\nBETA"))
        assert res.success
        raw = f.read_bytes()
        assert b"alpha\r\nBETA\r\ngamma\r\n" == raw, "CRLF convention must survive"

    def test_write_keeps_crlf_convention(self, tmp_path):
        f = tmp_path / "crlf.txt"
        f.write_bytes(b"one\r\ntwo\r\n")
        asyncio.run(WriteTool().execute(str(f), "one\ntwo\n"))
        assert f.read_bytes() == b"one\r\ntwo\r\n"


# ── 8. task reports are budgeted + persisted ─────────────────────────────────

class _FakeAgent:
    def __init__(self, content, role="worker", aid="sub-1"):
        from zouwucode.agent.subagent import AgentStatus
        self.role = role
        self.id = aid
        self.status = AgentStatus.COMPLETED
        self.result = ModelResponse(content=content)
        self.error = None
        self.duration = 1.0
        self.worktree = None


class _FakeManager:
    def __init__(self, agents, data_dir):
        class _Cfg:
            pass
        self.config = _Cfg()
        self.config.data_dir = str(data_dir)
        self._agents = agents

    async def run_parallel(self, tasks):
        return self._agents


class TestTaskReportBudget:
    def test_long_reports_are_budgeted_and_saved(self, tmp_path):
        from zouwucode.tools.agent_tools import TaskTool, _MAX_TASK_REPORT_CHARS
        agents = [_FakeAgent("x" * 5000, aid=f"sub-{i}") for i in range(3)]
        tool = TaskTool(_FakeManager(agents, tmp_path))
        res = asyncio.run(tool.execute(tasks=[{"task": "t"}]))

        assert res.success
        # Budget + headers + footer stay close to the cap (never the raw 15k).
        assert len(res.output) <= _MAX_TASK_REPORT_CHARS + 800
        reports = list((tmp_path / "agent_reports").glob("*.md"))
        assert len(reports) == 1
        assert len(reports[0].read_text(encoding="utf-8")) > 15_000

    def test_short_reports_pass_through(self, tmp_path):
        from zouwucode.tools.agent_tools import TaskTool
        agents = [_FakeAgent("short result", aid="sub-1")]
        tool = TaskTool(_FakeManager(agents, tmp_path))
        res = asyncio.run(tool.execute(tasks=[{"task": "t"}]))

        assert "short result" in res.output
        assert not (tmp_path / "agent_reports").exists()

    def test_clip_keeps_head_and_tail(self):
        from zouwucode.tools.agent_tools import _clip
        text = "HEAD" + "m" * 5000 + "TAIL"
        out = _clip(text, 1000)
        assert out.startswith("HEAD") and out.endswith("TAIL")
        assert len(out) <= 1000