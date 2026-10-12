"""Regression for v1.3.0: session /share (Markdown export) + cross-session
messaging (local SQLite bus, /msg /inbox /peers). Both are local-first: no
upload and no daemon.
"""

from pathlib import Path

from zouwucode.share import render_session_markdown, export_session
from zouwucode.messaging import MessageBus, format_inbox, format_peers

ROOT = Path(__file__).resolve().parent.parent


class TestShare:
    def test_render_markdown(self):
        data = {"session_id": "s1", "project_root": "/ws", "turns": [
            {"role": "user", "content": "hi", "timestamp": 0},
            {"role": "assistant", "content": "hello there", "timestamp": 0},
        ]}
        md = render_session_markdown(data)
        assert md.startswith("# ZOUWUCODE session s1")
        assert "### USER" in md and "### ASSISTANT" in md
        assert "hello there" in md and "`/ws`" in md

    def test_export_sanitises_and_writes(self, tmp_path):
        p = export_session({"session_id": "abc/def:1", "turns": []}, tmp_path)
        assert p.exists() and "/" not in p.name and ":" not in p.name
        assert p.read_text(encoding="utf-8").startswith("# ZOUWUCODE session")


class TestMessageBus:
    def _bus(self, tmp_path, agent):
        b = MessageBus(tmp_path / "m.db", agent=agent)
        b.register()
        return b

    def test_send_and_drain(self, tmp_path):
        a, b = self._bus(tmp_path, "A"), self._bus(tmp_path, "B")
        assert a.send("B", "hello B") >= 1
        msgs = b.drain()
        assert len(msgs) == 1 and msgs[0]["text"] == "hello B"
        assert b.drain() == []                    # drained exactly once

    def test_broadcast_excludes_sender(self, tmp_path):
        a, b = self._bus(tmp_path, "A"), self._bus(tmp_path, "B")
        a.send("*", "everyone")
        assert len(b.inbox()) == 1
        assert a.inbox() == []                    # sender doesn't receive own broadcast

    def test_peers_and_unread(self, tmp_path):
        a, b = self._bus(tmp_path, "A"), self._bus(tmp_path, "B")
        b.send("A", "ping from B")
        peers = a.peers()
        assert [p["agent"] for p in peers] == ["B"]
        assert peers[0]["unread"] == 1

    def test_mark_read(self, tmp_path):
        a, b = self._bus(tmp_path, "A"), self._bus(tmp_path, "B")
        mid = a.send("B", "x")
        assert b.mark_read([mid]) == 1
        assert b.inbox(unread_only=True) == []
        assert len(b.inbox(unread_only=False)) == 1

    def test_formatters_never_crash_on_empty(self):
        assert "empty" in format_inbox([])
        assert "no other" in format_peers([])


class TestAppWiring:
    def test_app_exposes_share_and_bus(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from zouwucode.tui.app import ZOUWUCODEApp
        app = ZOUWUCODEApp()
        try:
            assert app.bus is not None                       # messaging on by default
            assert app.bus.agent.startswith(tmp_path.name[:20])  # named after workspace
            app.data_dir = tmp_path                          # keep exports out of the repo
            r = app.share_current_session()
            assert r["ok"] and Path(r["path"]).exists()
            assert r["format"] == "markdown"
        finally:
            pass

    def test_messaging_can_be_disabled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from zouwucode.tui.app import ZOUWUCODEApp
        app = ZOUWUCODEApp()
        app.config.messaging.enabled = False
        assert app._init_message_bus() is None


class TestFrontendWiring:
    def test_cli_and_tui_commands(self):
        for rel in ("zouwucode/tui/app.py", "zouwucode/tui/textual_app.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            for needle in ('command == "/share"', 'command == "/msg"',
                           'command == "/inbox"', 'command == "/peers"'):
                assert needle in src, (rel, needle)

    def test_web_endpoints_and_commands(self):
        web = (ROOT / "zouwucode/webui/server.py").read_text(encoding="utf-8")
        for needle in ('"/api/share"', '"/api/msg"', '"/api/inbox"', '"/api/peers"',
                       "cmd.startsWith('/msg ')", "cmd === '/inbox'", "cmd === '/peers'"):
            assert needle in web, needle
