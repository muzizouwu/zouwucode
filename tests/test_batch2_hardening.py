"""Regression tests for the second hardening batch.

Covers the Web UI request-size guard, handler-level routes, atomic session
persistence with ``.bak`` recovery, and the snapshot throttle used for
assistant turns that ran no tools.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.session.manager import SessionManager
from zouwucode.webui.server import MAX_REQUEST_BYTES, WebUIServer


class _Writer:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, chunk: bytes):
        self.data += chunk

    async def drain(self):
        return None

    def close(self):
        self.closed = True


def _server(sessions=None, *, busy=False):
    engine = SimpleNamespace()
    srv = WebUIServer(
        engine=engine,
        tools=SimpleNamespace(get_schemas=lambda: []),
        coordinator=SimpleNamespace(),
        config=ZOUWUCODEConfig(),
    )
    srv.sessions_manager = sessions
    srv._chat_busy = busy
    return srv


def _request(method: str, path: str, body: bytes = b"", *,
             busy: bool = False, sessions=None) -> str:
    async def go():
        reader = asyncio.StreamReader()
        head = (f"{method} {path} HTTP/1.1\r\nHost: localhost\r\n"
                f"Content-Length: {len(body)}\r\n\r\n").encode("utf-8")
        reader.feed_data(head + body)
        reader.feed_eof()
        writer = _Writer()
        await _server(sessions, busy=busy).handle_request(reader, writer)
        return writer.data.decode("utf-8", "replace")

    return asyncio.run(go())


class TestRequestBodyLimit:
    def test_oversized_content_length_gets_413(self):
        async def go():
            reader = asyncio.StreamReader()
            reader.feed_data(
                b"POST /api/chat HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Length: 99999999\r\n\r\n" + b"x" * 32
            )
            reader.feed_eof()
            writer = _Writer()
            await _server().handle_request(reader, writer)
            return writer

        writer = asyncio.run(go())
        assert b"413 Payload Too Large" in writer.data
        assert writer.closed
        assert f"{MAX_REQUEST_BYTES // (1024 * 1024)} MB".encode("utf-8") in writer.data

    def test_limit_is_at_least_attachment_budget(self):
        # 32 MB raw attachments become ~43 MB of base64; the cap must exceed it.
        assert MAX_REQUEST_BYTES >= 48 * 1024 * 1024


class TestRouteHandlers:
    def test_invalid_json_is_rejected(self):
        resp = _request("POST", "/api/chat", b"{oops")
        assert "400" in resp.splitlines()[0]
        assert "Invalid JSON" in resp

    def test_busy_turn_returns_409(self):
        body = json.dumps({"message": "hi"}).encode("utf-8")
        resp = _request("POST", "/api/chat", body, busy=True)
        assert "409" in resp

    def test_sessions_route_lists_turns(self):
        class FakeSessions:
            current_session_id = None

            def list_sessions(self):
                return [{"id": "s1", "title": "demo", "project_root": "G:/x",
                         "turns": [{"role": "user"}], "updated": 1}]

        async def go():
            reader = asyncio.StreamReader()
            reader.feed_data(b"GET /api/sessions HTTP/1.1\r\nHost: localhost\r\n\r\n")
            reader.feed_eof()
            writer = _Writer()
            await _server(FakeSessions()).handle_request(reader, writer)
            return writer.data.decode("utf-8")

        payload = json.loads(asyncio.run(go()).split("\r\n\r\n", 1)[1])
        assert payload["sessions"][0]["title"] == "demo"

    def test_unknown_route_is_404(self):
        resp = _request("GET", "/api/nope")
        assert "404" in resp.splitlines()[0]


def _manager(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = ZOUWUCODEConfig()
    cfg.data_dir = str(tmp_path / "data")
    cfg.session.rollback_enabled = False
    return SessionManager(cfg)


class TestSessionPersistence:
    def test_bak_holds_previous_transcript(self, tmp_path, monkeypatch):
        mgr = _manager(tmp_path, monkeypatch)
        mgr.start_session("s1")
        mgr.log_turn({"role": "user", "content": "one"})
        path = mgr._sessions_dir / "s1.json"
        first = path.read_text(encoding="utf-8")
        mgr.log_turn({"role": "assistant", "content": "two"})
        bak = path.with_name("s1.json.bak")
        assert bak.read_text(encoding="utf-8") == first
        assert not path.with_name("s1.json.tmp").exists()

    def test_load_recovers_from_backup_and_quarantines_corrupt(self, tmp_path, monkeypatch):
        mgr = _manager(tmp_path, monkeypatch)
        mgr.start_session("s1")
        mgr.log_turn({"role": "user", "content": "one"})
        mgr.log_turn({"role": "assistant", "content": "two"})
        path = mgr._sessions_dir / "s1.json"
        path.write_text("{not json", encoding="utf-8")
        turns = mgr.load_session("s1")
        assert turns is not None and len(turns) == 1
        assert turns[0]["content"] == "one"
        assert not path.exists()
        assert list(mgr._sessions_dir.glob("s1.json.corrupt-*"))

    def test_get_session_falls_back_to_backup(self, tmp_path, monkeypatch):
        mgr = _manager(tmp_path, monkeypatch)
        mgr.start_session("s1")
        mgr.log_turn({"role": "user", "content": "one"})
        mgr.log_turn({"role": "assistant", "content": "two"})
        path = mgr._sessions_dir / "s1.json"
        path.write_text("garbage", encoding="utf-8")
        data = mgr.get_session("s1")
        assert data is not None and len(data["turns"]) == 1

    def test_delete_removes_backup(self, tmp_path, monkeypatch):
        mgr = _manager(tmp_path, monkeypatch)
        mgr.start_session("s1")
        mgr.log_turn({"role": "user", "content": "one"})
        mgr.log_turn({"role": "assistant", "content": "two"})
        assert mgr.delete_session("s1")
        assert not (mgr._sessions_dir / "s1.json.bak").exists()

    def test_snapshot_can_be_skipped_for_tool_free_turn(self, tmp_path, monkeypatch):
        class FakeSnapshotter:
            enabled = True

            def __init__(self):
                self.calls = 0

            def snapshot(self, label=""):
                self.calls += 1

            def head(self):
                return "0" * 40

        mgr = _manager(tmp_path, monkeypatch)
        mgr._snapshotter = FakeSnapshotter()
        mgr.start_session("s1")
        mgr.log_turn({"role": "assistant", "content": "no tools"}, snapshot=False)
        assert mgr._snapshotter.calls == 0
        mgr.log_turn({"role": "user", "content": "tools ran"})
        assert mgr._snapshotter.calls == 1
