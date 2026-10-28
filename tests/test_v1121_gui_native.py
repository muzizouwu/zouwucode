"""v1.12.1 — native GUI path + deeper session-route coverage.

Two gaps closed here:

1. `--gui` used to look identical to `--web` because pywebview was merely an
   optional extra: without it the launcher silently opened a browser. The
   native path is now covered explicitly (window created, configuration passed,
   graceful fallback on a broken toolkit backend).
2. The session routes added in v1.12.0 were only covered indirectly; each
   action is exercised against a fake session manager, including the failure
   modes (missing id / unknown session / a task already running).
"""

import asyncio
import json
import sys

import pytest

from zouwucode import gui as gui_mod


class _FakeWebview:
    def __init__(self, fail=False):
        self.fail = fail
        self.created = []
        self.started = 0

    def create_window(self, title, url, **kwargs):
        self.created.append((title, url, kwargs))
        if self.fail:
            raise RuntimeError("no webview backend")

    def start(self, *args, **kwargs):
        self.started += 1
        if self.fail:
            raise RuntimeError("backend crashed")


class TestNativeWindow:
    def test_window_is_created_with_the_gui_url(self, monkeypatch):
        fake = _FakeWebview()
        monkeypatch.setitem(sys.modules, "webview", fake)
        assert gui_mod._open_window("http://127.0.0.1:8080/") is True
        title, url, kwargs = fake.created[0]
        assert title == gui_mod._GUI_TITLE
        assert url == "http://127.0.0.1:8080/"
        assert kwargs["width"] == 1280 and kwargs["height"] == 860
        assert kwargs["min_size"] == (960, 600)
        assert fake.started == 1

    def test_broken_backend_falls_back(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "webview", _FakeWebview(fail=True))
        assert gui_mod._open_window("http://127.0.0.1:1/") is False

    def test_missing_package_falls_back(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "webview", None)
        assert gui_mod._open_window("http://127.0.0.1:1/") is False

    def test_launch_uses_native_window_when_available(self, monkeypatch, capsys):
        monkeypatch.setattr(gui_mod, "port_open", lambda *a, **k: False)
        monkeypatch.setattr(gui_mod, "_wait_for_server", lambda *a, **k: True)
        monkeypatch.setattr(gui_mod.threading, "Thread",
                            lambda *a, **k: type("T", (), {"start": lambda s: None})())
        monkeypatch.setattr(gui_mod, "_open_window", lambda url: True)

        code = gui_mod.launch_gui(object(), port=8123, block=False)
        out = capsys.readouterr().out
        assert code == 0
        assert "Native window opened" in out
        assert "http://127.0.0.1:8123/" in out


# ── session routes ──────────────────────────────────────────────────────────

class _FakeSessions:
    def __init__(self, exists=True):
        self.exists = exists
        self.calls = []
        self.forked = {"id": "fork-x", "turns": 3, "updated": 0,
                       "project_root": "/w"}

    def delete_session(self, sid):
        self.calls.append(("delete", sid))
        return self.exists

    def archive_session(self, sid):
        self.calls.append(("archive", sid))
        return self.exists

    def unarchive_session(self, sid):
        self.calls.append(("unarchive", sid))
        return self.exists

    def fork_session(self, sid, at_turn=None):
        self.calls.append(("fork", sid, at_turn))
        return self.forked if self.exists else None

    def list_archived(self):
        return [{"id": "old-1", "turns": 2, "updated": 0}]


class _Engine:
    on_thinking_delta = None
    on_tool_event = None
    is_running = False

    class stats:
        hit_rate = 0.0
        total_cost = 0.0
        total_requests = 0
        total_prompt_tokens = 0


def _server(sessions=None, busy=False):
    from zouwucode.config import ZOUWUCODEConfig
    from zouwucode.webui.server import WebUIServer

    srv = WebUIServer(engine=_Engine(), tools=type("T", (), {"get_schemas": lambda self: []})(),
                      coordinator=type("C", (), {"project_memory": None})(),
                      config=ZOUWUCODEConfig(), modules=None)
    srv.sessions_manager = sessions
    srv._chat_busy = busy
    return srv


def _payload(response: str) -> dict:
    return json.loads(response.split("\r\n\r\n", 1)[1])


def _call(srv, action, body):
    # _session_action is synchronous: it only builds a response string.
    return srv._session_action(body, action)


class TestSessionRoutes:
    @pytest.mark.parametrize("action", ["delete", "archive", "unarchive"])
    def test_simple_actions_dispatch(self, action):
        sessions = _FakeSessions()
        srv = _server(sessions)
        res = _payload(_call(srv, action, '{"id": "s-1"}'))
        assert res["ok"] is True
        assert sessions.calls == [(action, "s-1")]

    def test_missing_id_is_400(self):
        srv = _server(_FakeSessions())
        response = _call(srv, "delete", "{}")
        assert response.startswith("HTTP/1.1 400")
        assert "missing id" in _payload(response)["error"]

    def test_invalid_json_is_400(self):
        srv = _server(_FakeSessions())
        response = _call(srv, "archive", "{not json")
        assert response.startswith("HTTP/1.1 400")

    def test_unknown_session_is_404(self):
        srv = _server(_FakeSessions(exists=False))
        response = _call(srv, "delete", '{"id": "nope"}')
        assert response.startswith("HTTP/1.1 404")
        assert _payload(response)["ok"] is False

    def test_running_task_blocks_mutation(self):
        sessions = _FakeSessions()
        srv = _server(sessions, busy=True)
        response = _call(srv, "delete", '{"id": "s-1"}')
        assert response.startswith("HTTP/1.1 409")
        assert sessions.calls == [], "运行中的任务不得触发会话写操作"

    def test_no_session_manager_is_reported(self):
        srv = _server(None)
        srv.sessions_manager = None
        response = _call(srv, "archive", '{"id": "s-1"}')
        assert response.startswith("HTTP/1.1 400")
        assert "sessions unavailable" in _payload(response)["error"]

    def test_fork_route_passes_truncation_point(self):
        from zouwucode.config import ZOUWUCODEConfig
        from zouwucode.webui.server import WebUIServer

        sessions = _FakeSessions()
        srv = WebUIServer(engine=_Engine(), tools=type("T", (), {"get_schemas": lambda self: []})(),
                          coordinator=type("C", (), {"project_memory": None})(),
                          config=ZOUWUCODEConfig(), modules=None)
        srv.sessions_manager = sessions
        response = asyncio.run(
            srv._route("POST", "/api/session/fork", '{"id": "s-1", "turn": 4}'))
        body = _payload(response)
        assert body["ok"] is True and body["id"] == "fork-x"
        assert sessions.calls[-1] == ("fork", "s-1", 4)

    def test_archived_listing_route(self):
        from zouwucode.config import ZOUWUCODEConfig
        from zouwucode.webui.server import WebUIServer

        srv = WebUIServer(engine=_Engine(), tools=type("T", (), {"get_schemas": lambda self: []})(),
                          coordinator=type("C", (), {"project_memory": None})(),
                          config=ZOUWUCODEConfig(), modules=None)
        srv.sessions_manager = _FakeSessions()
        body = _payload(asyncio.run(srv._route("GET", "/api/sessions/archived", "")))
        assert body["sessions"][0]["id"] == "old-1"


class TestArchivedInterop:
    """Archived really is a view: read / fork / resume keep working."""

    @staticmethod
    def _mgr(tmp_path, monkeypatch):
        from zouwucode.config import ZOUWUCODEConfig
        from zouwucode.session.manager import SessionManager

        monkeypatch.chdir(tmp_path)
        cfg = ZOUWUCODEConfig()
        cfg.data_dir = str(tmp_path / "data")
        cfg.session.rollback_enabled = False
        mgr = SessionManager(cfg)
        mgr.start_session("arch-1")
        mgr.log_turn({"role": "user", "content": "hello"})
        mgr.log_turn({"role": "assistant", "content": "hi"})
        mgr.archive_session("arch-1")
        return mgr

    def test_fork_archived_session(self, tmp_path, monkeypatch):
        mgr = self._mgr(tmp_path, monkeypatch)
        forked = mgr.fork_session("arch-1", at_turn=1, new_id="fork-arch")
        assert forked["turns"] == 1
        assert mgr.get_session("fork-arch")["forked_from"] == "arch-1"

    def test_unarchive_then_load(self, tmp_path, monkeypatch):
        mgr = self._mgr(tmp_path, monkeypatch)
        assert mgr.unarchive_session("arch-1") is True
        assert [t["content"] for t in mgr.load_session("arch-1")] == ["hello", "hi"]

    def test_delete_archived_session(self, tmp_path, monkeypatch):
        mgr = self._mgr(tmp_path, monkeypatch)
        (mgr._archive_dir / "arch-1.json").unlink()
        assert mgr.list_archived() == []
