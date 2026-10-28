"""v1.13.0 — session titles (auto + rename) and workspace switching."""

import asyncio
import inspect
import json

import pytest

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.session.manager import SessionManager, derive_title
from zouwucode.webui import server


def _mgr(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = ZOUWUCODEConfig()
    cfg.data_dir = str(tmp_path / "data")
    cfg.session.rollback_enabled = False
    return SessionManager(cfg)


class TestDeriveTitle:
    def test_uses_first_user_turn(self):
        assert derive_title([{"role": "system", "content": "x"},
                             {"role": "user", "content": "帮我把测试修好"}]) == "帮我把测试修好"

    def test_collapses_whitespace(self):
        assert derive_title([{"role": "user", "content": "  多行\n\n  任务  "}]) == "多行 任务"

    def test_truncates_with_ellipsis(self):
        title = derive_title([{"role": "user", "content": "长" * 80}])
        assert len(title) == 40 and title.endswith("…")

    def test_falls_back_when_no_user_turn(self):
        assert derive_title([]) == "新会话"
        assert derive_title([{"role": "assistant", "content": "hi"}]) == "新会话"


class TestAutoTitle:
    def test_saved_session_exposes_title(self, tmp_path, monkeypatch):
        mgr = _mgr(tmp_path, monkeypatch)
        mgr.start_session("s-1")
        mgr.log_turn({"role": "user", "content": "重构配置加载"})
        entries = mgr.list_sessions()
        assert entries[0]["title"] == "重构配置加载"

    def test_title_survives_reload(self, tmp_path, monkeypatch):
        mgr = _mgr(tmp_path, monkeypatch)
        mgr.start_session("s-1")
        mgr.log_turn({"role": "user", "content": "分析仓库结构"})
        fresh = _mgr(tmp_path, monkeypatch)
        assert fresh.get_session("s-1")["title"] == "分析仓库结构"

    def test_forked_session_is_marked(self, tmp_path, monkeypatch):
        mgr = _mgr(tmp_path, monkeypatch)
        mgr.start_session("s-1")
        mgr.log_turn({"role": "user", "content": "修复 bug"})
        forked = mgr.fork_session("s-1", new_id="fork-1")
        assert forked["title"].endswith("· 分支")


class TestRename:
    def test_rename_active_session(self, tmp_path, monkeypatch):
        mgr = _mgr(tmp_path, monkeypatch)
        mgr.start_session("s-1")
        mgr.log_turn({"role": "user", "content": "原始标题"})
        assert mgr.rename_session("s-1", "  自定义 名称 ") is True
        assert mgr.get_session("s-1")["title"] == "自定义 名称"
        assert mgr.list_sessions()[0]["title"] == "自定义 名称"

    def test_rename_archived_session(self, tmp_path, monkeypatch):
        mgr = _mgr(tmp_path, monkeypatch)
        mgr.start_session("s-1")
        mgr.log_turn({"role": "user", "content": "旧会话"})
        mgr.archive_session("s-1")
        assert mgr.rename_session("s-1", "归档后改名") is True
        assert mgr.list_archived()[0]["title"] == "归档后改名"

    def test_empty_title_rejected(self, tmp_path, monkeypatch):
        mgr = _mgr(tmp_path, monkeypatch)
        mgr.start_session("s-1")
        mgr.log_turn({"role": "user", "content": "x"})
        assert mgr.rename_session("s-1", "   ") is False

    def test_unknown_session_rejected(self, tmp_path, monkeypatch):
        mgr = _mgr(tmp_path, monkeypatch)
        assert mgr.rename_session("nope", "x") is False


class _FakeSessions:
    def __init__(self):
        self.calls = []
        self.entries = [{"id": "s-1", "title": "T", "turns": 1,
                         "updated": 0, "project_root": ""}]

    def rename_session(self, sid, title):
        self.calls.append((sid, title))
        return sid != "missing"

    def list_sessions(self):
        return list(self.entries)


class _Engine:
    on_thinking_delta = None
    on_tool_event = None
    is_running = False

    class stats:
        hit_rate = 0.0
        total_cost = 0.0
        total_requests = 0
        total_prompt_tokens = 0


class _App:
    def __init__(self, result=None):
        self.result = result or {"ok": True, "workspace": "/w", "changed": True}

    def switch_workspace(self, path):
        self.result["workspace"] = path
        return self.result


def _srv(sessions=None, app=None, busy=False):
    from zouwucode.webui.server import WebUIServer

    srv = WebUIServer(engine=_Engine(), tools=type("T", (), {"get_schemas": lambda self: []})(),
                      coordinator=type("C", (), {"project_memory": None})(),
                      config=ZOUWUCODEConfig(), modules=None)
    srv.sessions_manager = sessions
    if app is not None:
        srv.app = app
    srv._chat_busy = busy
    return srv


class TestRenameRoute:
    def _post(self, srv, body):
        return asyncio.run(srv._route("POST", "/api/session/rename", body))

    def test_rename_ok(self):
        sessions = _FakeSessions()
        body = json.loads(self._post(_srv(sessions), '{"id":"s-1","title":" 新名字 "}')
                          .split("\r\n\r\n", 1)[1])
        assert body == {"ok": True, "id": "s-1", "title": "新名字"}
        assert sessions.calls == [("s-1", "新名字")]

    def test_missing_fields_400(self):
        response = self._post(_srv(_FakeSessions()), '{"id":"s-1"}')
        assert response.startswith("HTTP/1.1 400")

    def test_unknown_session_404(self):
        response = self._post(_srv(_FakeSessions()), '{"id":"missing","title":"x"}')
        assert response.startswith("HTTP/1.1 404")

    def test_no_manager_400(self):
        srv = _srv(None)
        srv.sessions_manager = None
        assert self._post(srv, '{"id":"s-1","title":"x"}').startswith("HTTP/1.1 400")


class TestWorkspaceRoutes:
    def test_listing_reports_current(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        srv = _srv(_FakeSessions())
        body = asyncio.run(srv._route("GET", "/api/workspaces", ""))
        payload = json.loads(body.split("\r\n\r\n", 1)[1])
        assert payload["current"] == str(tmp_path.resolve())
        assert payload["recent"] == []  # empty project_root is filtered out

    def test_switch_forwards_path(self):
        app = _App()
        srv = _srv(_FakeSessions(), app=app)
        response = asyncio.run(
            srv._route("POST", "/api/workspace/switch", '{"path":"/tmp/x"}'))
        assert response.startswith("HTTP/1.1 200")
        payload = json.loads(response.split("\r\n\r\n", 1)[1])
        assert payload["workspace"] == "/tmp/x"

    def test_switch_missing_path_400(self):
        srv = _srv(_FakeSessions(), app=_App())
        response = asyncio.run(srv._route("POST", "/api/workspace/switch", "{}"))
        assert response.startswith("HTTP/1.1 400")

    def test_switch_blocked_while_running(self):
        srv = _srv(_FakeSessions(), app=_App())
        srv._chat_busy = True
        response = asyncio.run(
            srv._route("POST", "/api/workspace/switch", '{"path":"/tmp/x"}'))
        assert response.startswith("HTTP/1.1 409")

    def test_switch_without_app_400(self):
        srv = _srv(_FakeSessions())
        del srv.app
        response = asyncio.run(
            srv._route("POST", "/api/workspace/switch", '{"path":"/tmp/x"}'))
        assert response.startswith("HTTP/1.1 400")

    def test_bad_target_returns_error(self):
        srv = _srv(_FakeSessions(), app=_App(
            {"ok": False, "error": "not a directory"}))
        response = asyncio.run(
            srv._route("POST", "/api/workspace/switch", '{"path":"/nope"}'))
        assert response.startswith("HTTP/1.1 400")


class TestAppSwitchWorkspace:
    def test_rejects_non_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from zouwucode.tui.app import ZOUWUCODEApp

        app = ZOUWUCODEApp()
        result = app.switch_workspace(str(tmp_path / "missing"))
        assert result["ok"] is False and "directory" in result["error"]

    def test_switches_cwd_scope_and_session(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from pathlib import Path
        from zouwucode.tui.app import ZOUWUCODEApp

        app = ZOUWUCODEApp()
        older = app.sessions.current_session_id
        target = tmp_path / "other-project"
        target.mkdir()

        result = app.switch_workspace(str(target))
        assert result["ok"] and result["changed"] is True
        assert Path.cwd().resolve() == target.resolve()
        assert app.sandbox._workspace_root == target.resolve()
        assert app.sessions.current_session_id != older
        assert result["session_id"] == app.sessions.current_session_id

    def test_same_directory_is_a_noop(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from zouwucode.tui.app import ZOUWUCODEApp

        app = ZOUWUCODEApp()
        sid = app.sessions.current_session_id
        result = app.switch_workspace(str(tmp_path))
        assert result["ok"] and result["changed"] is False
        assert app.sessions.current_session_id == sid


class TestFrontendContract:
    INDEX = server.read_static("index.html")[0]
    JS = server.read_static("app.js")[0]
    CSS = server.read_static("style.css")[0]

    def test_workspace_picker_present(self):
        for node in ("workspace-btn", "workspace-name", "workspace-modal",
                     "workspace-list", "workspace-path", "workspace-open"):
            assert f'id="{node}"' in self.INDEX, node
        assert "/api/workspaces" in self.JS
        assert "/api/workspace/switch" in self.JS
        assert ".workspace-btn" in self.CSS

    def test_rename_and_titles_present(self):
        assert '"rename"' in self.JS and "/api/session/rename" in self.JS
        assert "item.title || item.id" in self.JS, "侧栏应显示标题而不是裸 id"
        assert "window.prompt(" in self.JS
        assert "auto-title" in self.JS or "loadSessions();" in self.JS

    def test_escape_closes_workspace_modal(self):
        # The global handler is the last Escape branch (palette has its own).
        idx = self.JS.rindex('ev.key === "Escape"')
        assert "workspace-modal" in self.JS[idx:idx + 400]
