"""v1.12.0 — session lifecycle in the GUI: restore, archive, delete, fork,
plus the Trae-style trajectory rail.

Reported from real use: opening the Web UI looked like "last conversation
vanished" (the view booted empty and sessions had no actions), old sessions
could not be deleted or archived, and there was no way to branch a
conversation. This pins the backend operations and the frontend contract.
"""

import inspect

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.session.manager import SessionManager
from zouwucode.webui import server


def _manager(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = ZOUWUCODEConfig()
    cfg.data_dir = str(tmp_path / "data")
    cfg.session.rollback_enabled = False   # no git snapshots in unit tests
    cfg.session.save_enabled = True
    return SessionManager(cfg)


def _seed(mgr, sid="s-1"):
    mgr.start_session(sid)
    mgr.log_turn({"role": "user", "content": "hello"})
    mgr.log_turn({"role": "assistant", "content": "hi there"})
    return sid


class TestArchive:
    def test_archive_moves_out_of_active_list(self, tmp_path, monkeypatch):
        mgr = _manager(tmp_path, monkeypatch)
        _seed(mgr)
        assert [s["id"] for s in mgr.list_sessions()] == ["s-1"]

        assert mgr.archive_session("s-1") is True
        assert mgr.list_sessions() == []
        assert [s["id"] for s in mgr.list_archived()] == ["s-1"]

    def test_unarchive_restores_it(self, tmp_path, monkeypatch):
        mgr = _manager(tmp_path, monkeypatch)
        _seed(mgr)
        mgr.archive_session("s-1")
        assert mgr.unarchive_session("s-1") is True
        assert [s["id"] for s in mgr.list_sessions()] == ["s-1"]
        assert mgr.list_archived() == []

    def test_unknown_session_returns_false(self, tmp_path, monkeypatch):
        mgr = _manager(tmp_path, monkeypatch)
        assert mgr.archive_session("nope") is False
        assert mgr.unarchive_session("nope") is False

    def test_archived_session_can_still_be_loaded(self, tmp_path, monkeypatch):
        """Archiving is a UI view, not data loss."""
        mgr = _manager(tmp_path, monkeypatch)
        _seed(mgr)
        mgr.archive_session("s-1")
        assert mgr.get_session("s-1")["turns"][0]["content"] == "hello"


class TestFork:
    def test_fork_copies_whole_conversation(self, tmp_path, monkeypatch):
        mgr = _manager(tmp_path, monkeypatch)
        _seed(mgr)
        forked = mgr.fork_session("s-1", new_id="fork-1")
        assert forked == {"id": "fork-1", "turns": 2, "updated": forked["updated"],
                          "project_root": forked["project_root"]}
        assert mgr.get_session("fork-1")["forked_from"] == "s-1"

    def test_fork_can_truncate_at_a_message(self, tmp_path, monkeypatch):
        mgr = _manager(tmp_path, monkeypatch)
        _seed(mgr)
        forked = mgr.fork_session("s-1", at_turn=1, new_id="fork-2")
        assert forked["turns"] == 1
        assert mgr.get_session("fork-2")["turns"][0]["content"] == "hello"

    def test_fork_does_not_touch_the_original(self, tmp_path, monkeypatch):
        mgr = _manager(tmp_path, monkeypatch)
        _seed(mgr)
        mgr.fork_session("s-1", at_turn=0, new_id="fork-3")
        assert len(mgr.get_session("s-1")["turns"]) == 2

    def test_fork_unknown_returns_none(self, tmp_path, monkeypatch):
        mgr = _manager(tmp_path, monkeypatch)
        assert mgr.fork_session("nope") is None

    def test_generated_fork_id_is_unique(self, tmp_path, monkeypatch):
        mgr = _manager(tmp_path, monkeypatch)
        _seed(mgr)
        a = mgr.fork_session("s-1")["id"]
        b = mgr.fork_session("s-1")["id"]
        assert a != b and a.startswith("fork-")


class TestDelete:
    def test_delete_removes_file(self, tmp_path, monkeypatch):
        mgr = _manager(tmp_path, monkeypatch)
        _seed(mgr)
        assert mgr.delete_session("s-1") is True
        assert mgr.list_sessions() == []
        assert mgr.get_session("s-1") is None


class TestServerRoutes:
    @staticmethod
    def _src():
        from zouwucode.webui.server import WebUIServer
        return inspect.getsource(WebUIServer)

    def test_all_session_routes_registered(self):
        src = self._src()
        for route in ('"/api/sessions/archived"', '"/api/session/delete"',
                      '"/api/session/archive"', '"/api/session/unarchive"',
                      '"/api/session/fork"'):
            assert route in src, route

    def test_session_action_guards_running_task(self):
        src = self._src()
        idx = src.index("def _session_action")
        assert "_chat_busy" in src[idx:idx + 900]

    def test_archive_and_delete_are_reversible_or_explicit(self):
        src = self._src()
        assert "archive_session" in src and "unarchive_session" in src
        assert "delete_session" in src and "fork_session" in src


class TestFrontendContract:
    INDEX = server.read_static("index.html")[0]
    JS = server.read_static("app.js")[0]
    CSS = server.read_static("style.css")[0]

    def test_trajectory_rail_exists(self):
        """Trae-style dots: a rail that can jump to any message."""
        assert 'id="markers"' in self.INDEX
        assert "addMarker" in self.JS and "syncMarkers" in self.JS
        assert ".markers" in self.CSS and ".marker.active" in self.CSS

    def test_boot_restores_last_conversation(self):
        assert "bootstrapSession" in self.JS
        assert "/api/session/current" in self.JS
        assert "已恢复上次会话" in self.JS

    def test_session_actions_present(self):
        for glyph in ("fork", "archive", "unarchive", "delete"):
            assert f'"{glyph}"' in self.JS, glyph
        assert "/api/session/fork" in self.JS
        assert "/api/session/archive" in self.JS
        assert 'window.confirm(' in self.JS, "删除必须二次确认"

    def test_archived_section_present(self):
        assert 'id="archived-box"' in self.INDEX
        assert 'id="archived-list"' in self.INDEX
        assert "/api/sessions/archived" in self.JS

    def test_fork_at_message(self):
        assert 'class="fork-here"' in self.JS
        assert "forkAt" in self.JS
        assert ".fork-here" in self.CSS
