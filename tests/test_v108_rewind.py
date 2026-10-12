"""Regression for v1.0.8: per-message rewind (Claude Code /rewind, Cline checkpoints).

Pick any point in a session, then restore the workspace (code), the turn log
(chat), or both. Backed by side-git checkpoints bound to message indices.
"""

from pathlib import Path

import pytest

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.session.manager import SessionManager

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _cfg(tmp_path):
    c = ZOUWUCODEConfig()
    c.data_dir = str(tmp_path / "data")
    c.session.rollback_enabled = True
    return c


class TestCheckpointTracked:
    def test_baseline_and_per_turn(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        m = SessionManager(_cfg(tmp_path))
        m.start_session("s")
        m.snapshot_workspace("session start")
        m.record_checkpoint(0)
        (tmp_path / "a.txt").write_text("v1", encoding="utf-8")
        m.log_turn({"role": "user", "content": "hi"})
        (tmp_path / "a.txt").write_text("v2", encoding="utf-8")
        m.log_turn({"role": "assistant", "content": "ok"})
        cps = m.list_checkpoints()
        assert [c["index"] for c in cps] == [0, 1, 2]

    def test_checkpoints_persist_and_reload(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        m = SessionManager(_cfg(tmp_path))
        m.start_session("s")
        m.snapshot_workspace("start")
        m.record_checkpoint(0)
        (tmp_path / "f").write_text("x", encoding="utf-8")
        m.log_turn({"role": "user", "content": "hi"})
        m2 = SessionManager(_cfg(tmp_path))
        m2.load_session("s")
        assert [c["index"] for c in m2.list_checkpoints()] == [0, 1]


class TestRewind:
    def _seed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        m = SessionManager(_cfg(tmp_path))
        m.start_session("s")
        m.snapshot_workspace("start")
        m.record_checkpoint(0)
        (tmp_path / "a.txt").write_text("v1", encoding="utf-8")
        m.log_turn({"role": "user", "content": "make a"})
        (tmp_path / "a.txt").write_text("v2", encoding="utf-8")
        (tmp_path / "b.txt").write_text("new", encoding="utf-8")
        m.log_turn({"role": "assistant", "content": "made b"})
        return m

    def test_both_restores_files_and_conversation(self, tmp_path, monkeypatch):
        m = self._seed(tmp_path, monkeypatch)
        assert len(m.current_log()) == 2
        res = m.rewind(1)  # keep only the first message
        assert res["ok"] and res["files"] and res["conversation"]
        assert len(m.current_log()) == 1
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "v1"
        assert not (tmp_path / "b.txt").exists()

    def test_files_only_keeps_conversation(self, tmp_path, monkeypatch):
        m = self._seed(tmp_path, monkeypatch)
        res = m.rewind(1, files=True, conversation=False)
        assert res["files"] and not res["conversation"]
        assert len(m.current_log()) == 2            # chat untouched
        assert not (tmp_path / "b.txt").exists()    # code rewound
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "v1"

    def test_conversation_only_keeps_files(self, tmp_path, monkeypatch):
        m = self._seed(tmp_path, monkeypatch)
        res = m.rewind(1, files=False, conversation=True)
        assert res["conversation"] and not res["files"]
        assert len(m.current_log()) == 1
        assert (tmp_path / "b.txt").exists()        # code untouched
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "v2"

    def test_rewind_to_zero_is_baseline(self, tmp_path, monkeypatch):
        m = self._seed(tmp_path, monkeypatch)
        res = m.rewind(0)
        assert res["ok"] and res["files"]
        assert m.current_log() == []
        assert not (tmp_path / "a.txt").exists()

    def test_out_of_range_refused(self, tmp_path, monkeypatch):
        m = self._seed(tmp_path, monkeypatch)
        assert not m.rewind(-1)["ok"]
        assert not m.rewind(99)["ok"]

    def test_rewind_truncates_checkpoints(self, tmp_path, monkeypatch):
        m = self._seed(tmp_path, monkeypatch)
        m.rewind(1)
        assert [c["index"] for c in m.list_checkpoints()] == [0, 1]


class TestAppWiring:
    def test_rewind_to_rebuilds_context(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from zouwucode.tui.app import ZOUWUCODEApp
        app = ZOUWUCODEApp()
        app.initialize_session()
        app.sessions.log_turn({"role": "user", "content": "q1"})
        app.sessions.log_turn({"role": "assistant", "content": "a1"})
        app.sessions.record_checkpoint(1)
        app.sessions.log_turn({"role": "user", "content": "q2"})
        # rebuild engine so its cache reflects the log, then rewind
        app._rebuild_context_from_log()
        assert any(m.get("content") == "q2" for m in app.engine.cache.get_prefix())
        res = app.rewind_to(2, "chat")  # drop q2
        assert res["ok"] and res["conversation"]
        # q2 is gone from BOTH the log and the live engine cache
        assert len(app.sessions.current_log()) == 2
        assert not any(m.get("content") == "q2" for m in app.engine.cache.get_prefix())

    def test_three_frontends_expose_rewind(self):
        for rel in ("zouwucode/tui/app.py", "zouwucode/tui/textual_app.py"):
            src = _read(rel)
            assert "def rewind_to" in src
            assert 'command == "/rewind"' in src
        web = _read("zouwucode/webui/server.py")
        assert '"/api/rewind"' in web and '"/api/checkpoints"' in web
        assert "cmd.startsWith('/rewind')" in web
