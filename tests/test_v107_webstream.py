"""Regression for v1.0.7: Web body streaming + Web session resume.

Completes three-frontend parity: the browser now streams the assistant BODY
(not only thinking) and can resume a saved session mid-run (/resume), which
CLI/TUI could already do via --resume/--session.
"""

import asyncio
from pathlib import Path

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.session.manager import SessionManager

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestWebBodyStreaming:
    src = None

    @classmethod
    def setup_class(cls):
        cls.src = _read("zouwucode/webui/server.py")
        cls.js = _read("zouwucode/webui/static/app.js")

    def test_backend_emits_content_deltas(self):
        assert '"type": "content", "delta": delta' in self.src
        assert "self.engine.on_content_delta = _on_content" in self.src

    def test_backend_clears_content_hook(self):
        assert "self.engine.on_content_delta = None" in self.src

    def test_done_marks_streamed(self):
        assert '"streamed": streamed_body["any"]' in self.src

    def test_js_renders_content_events(self):
        assert "ensureContent" in self.js
        assert 'case "content"' in self.js
        assert "textContent += evt.delta" in self.js

    def test_js_avoids_double_content(self):
        # 后端已流式发送正文时，done 事件不再整段重绘（避免内容重复）
        assert "evt.streamed" in self.js


class TestWebResume:
    src = None

    @classmethod
    def setup_class(cls):
        cls.src = _read("zouwucode/webui/server.py")
        cls.js = _read("zouwucode/webui/static/app.js")

    def test_app_method_exists(self):
        assert "def resume_session(self, session_id" in _read("zouwucode/tui/app.py")

    def test_endpoints_present(self):
        assert '"/api/session/current"' in self.src
        assert '"/api/session/resume"' in self.src

    def test_app_is_wired_into_server(self):
        assert "app=None," in self.src
        assert "self.app = app" in self.src
        assert "app=self," in _read("zouwucode/tui/app.py")

    def test_js_resume_command(self):
        assert '{ cmd: "/resume"' in self.js
        assert "/api/session/resume" in self.js
        assert "/api/session/resume" in self.src

    def test_resume_guards_against_concurrent_turn(self):
        idx = self.src.index('"/api/session/resume"')
        assert "_chat_busy" in self.src[idx:idx + 700]


class TestResumeFunctional:
    def test_resume_replays_into_engine(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from zouwucode.tui.app import ZOUWUCODEApp
        app = ZOUWUCODEApp()
        # create and persist a session with two turns
        sid = app.sessions.start_session("saved-1")
        app.sessions.log_turn({"role": "user", "content": "hello"})
        app.sessions.log_turn({"role": "assistant", "content": "hi there"})

        # fresh app (separate engine) resumes it
        app2 = ZOUWUCODEApp()
        turns = app2.resume_session("saved-1")
        assert [t["role"] for t in turns] == ["user", "assistant"]
        assert app2._session_id == "saved-1"
        # history was replayed into the live engine cache (not just loaded)
        contents = [m.get("content") for m in app2.engine.cache.get_prefix()]
        assert "hello" in contents and "hi there" in contents

    def test_resume_unknown_id_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from zouwucode.tui.app import ZOUWUCODEApp
        app = ZOUWUCODEApp()
        assert app.resume_session("does-not-exist") == []
