"""Cross-session continuity: --resume, history replay, useful summaries,
and the session date anchor in the system prompt.

The defects these pin:
- SessionManager.load_session had zero callers: "continue last task" never
  worked, the UI could list sessions but not resume one.
- Even a loaded session reached the model as an EMPTY conversation
  (freeze_session seeds only the system prompt) -> replay is required.
- Exit summaries were built from a process-local LLM round counter, which
  once produced a wall of "Session with 0 turns" entries.
- The system prompt had no date at all; the model had no time anchor.
"""

import datetime
import inspect
from pathlib import Path

import pytest

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.context.sliding_window import ContextWindow
from zouwucode.project_memory import ProjectMemory
from zouwucode.session.manager import SessionManager


class _FakeCache:
    """Stand-in for engine.PrefixCache — replay only needs append()."""

    def __init__(self):
        self.messages = []

    def append(self, message):
        self.messages.append(message)


@pytest.fixture
def cfg(tmp_path):
    c = ZOUWUCODEConfig()
    c.data_dir = str(tmp_path)
    return c


class TestSessionResume:
    def test_saved_session_records_project_root(self, cfg):
        m = SessionManager(cfg)
        m.start_session()
        m.log_turn({"role": "user", "content": "hello"})
        raw = SessionManager(cfg).get_session(m.current_session_id)
        assert raw["project_root"] == str(Path.cwd().resolve())

    def test_latest_session_finds_this_workspace(self, cfg):
        m = SessionManager(cfg)
        m.start_session()
        m.log_turn({"role": "user", "content": "hello"})
        sid = m.current_session_id
        latest = SessionManager(cfg).latest_session()
        assert latest is not None and latest["id"] == sid

    def test_latest_session_none_when_empty(self, cfg):
        assert SessionManager(cfg).latest_session() is None

    def test_load_session_restores_turns(self, cfg):
        m = SessionManager(cfg)
        m.start_session()
        m.log_turn({"role": "user", "content": "Q1"})
        sid = m.current_session_id
        m2 = SessionManager(cfg)
        turns = m2.load_session(sid)
        assert turns and turns[0]["content"] == "Q1"
        assert m2.current_log()[0]["content"] == "Q1"

    def test_load_missing_session_returns_none(self, cfg):
        assert SessionManager(cfg).load_session("session-does-not-exist") is None


class TestReplayHistory:
    def test_replays_user_and_assistant_skips_system(self):
        cache, ctx = _FakeCache(), ContextWindow()
        entries = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second", "thinking": "x"},
            {"role": "system", "content": "[interrupted] Esc"},
            {"role": "user", "content": ""},
            {"role": "user", "content": "third"},
        ]
        n = SessionManager.replay_history(entries, cache, ctx)
        assert n == 3
        assert [m["role"] for m in cache.messages] == ["user", "assistant", "user"]
        assert [m["content"] for m in cache.messages] == ["first", "second", "third"]
        assert ctx.get_stats()["warm_messages"] == 3

    def test_empty_history_is_a_noop(self):
        cache, ctx = _FakeCache(), ContextWindow()
        assert SessionManager.replay_history([], cache, ctx) == 0
        assert SessionManager.replay_history(None, cache, ctx) == 0
        assert cache.messages == []


class TestSessionSummary:
    def test_counts_user_turns_not_llm_rounds(self):
        turns = [
            {"role": "user", "content": "do X"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "now Y"},
        ]
        summary, n = ProjectMemory.build_session_summary(turns, 0.5)
        assert n == 2
        assert "2 turns" in summary
        assert "50.0%" in summary
        assert "last request: now Y" in summary

    def test_empty_log_still_sane(self):
        summary, n = ProjectMemory.build_session_summary([], 0.0)
        assert n == 0 and "0 turns" in summary


class TestTimeAnchor:
    def test_tui_prompt_carries_current_date(self):
        from zouwucode.tui.textual_app import ZOUWUCODETUI
        app = ZOUWUCODETUI()
        prompt = app._build_system_prompt()
        assert str(datetime.date.today().year) in prompt
        assert "Current date:" in prompt

    def test_both_prompt_builders_inject_the_date(self):
        from zouwucode.tui import app as cli_mod
        from zouwucode.tui import textual_app as tui_mod
        assert "Current date:" in inspect.getsource(
            cli_mod.ZOUWUCODEApp._build_system_prompt)
        assert "Current date:" in inspect.getsource(
            tui_mod.ZOUWUCODETUI._build_system_prompt)
