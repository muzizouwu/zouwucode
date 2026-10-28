"""v1.11.0 — GUI (desktop shell) + shared frontend + TUI restyle.

The GUI intentionally reuses the Web UI frontend, so the risks worth pinning
are: (1) the launcher must never dead-end when the desktop toolkit is absent,
(2) it must not silently bind a port that is already taken, and (3) the TUI
theme must style only selectors that actually exist (the old ``.message-*``
rules were dead code).
"""

import sys

import pytest

from zouwucode import gui as gui_mod
from zouwucode.webui import server


class _StubApp:
    def __init__(self):
        self.calls = []

    async def run_web(self, port=8080):
        self.calls.append(port)


class TestLauncher:
    def test_port_busy_is_reported_not_hijacked(self, monkeypatch, capsys):
        monkeypatch.setattr(gui_mod, "port_open", lambda *a, **k: True)
        code = gui_mod.launch_gui(_StubApp(), port=8080, block=False)
        assert code == 2
        assert "already in use" in capsys.readouterr().err

    def test_server_that_never_starts_is_reported(self, monkeypatch, capsys):
        monkeypatch.setattr(gui_mod, "port_open", lambda *a, **k: False)
        monkeypatch.setattr(gui_mod, "_wait_for_server", lambda *a, **k: False)
        monkeypatch.setattr(gui_mod.threading, "Thread",
                            lambda *a, **k: type("T", (), {"start": lambda s: None})())
        code = gui_mod.launch_gui(_StubApp(), port=8080, block=False)
        assert code == 1
        assert "did not start" in capsys.readouterr().err

    def test_browser_fallback_when_no_desktop_toolkit(self, monkeypatch, capsys):
        opened = {}
        monkeypatch.setattr(gui_mod, "port_open", lambda *a, **k: False)
        monkeypatch.setattr(gui_mod, "_wait_for_server", lambda *a, **k: True)
        monkeypatch.setattr(gui_mod.threading, "Thread",
                            lambda *a, **k: type("T", (), {"start": lambda s: None})())
        monkeypatch.setattr(gui_mod, "_open_window", lambda url: False)
        monkeypatch.setattr(gui_mod.webbrowser, "open",
                            lambda url, new=0: opened.setdefault("url", url) or True)

        code = gui_mod.launch_gui(_StubApp(), port=9099, block=False)
        assert code == 0
        assert opened["url"] == "http://127.0.0.1:9099/"
        out = capsys.readouterr().out
        assert "zouwucode[gui]" in out, "缺少安装原生窗口的提示"

    def test_native_window_without_pywebview_returns_false(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "webview", None)  # import → ImportError
        assert gui_mod._open_window("http://127.0.0.1:1/") is False

    def test_port_open_probe(self):
        # Port 1 is never a listening service on a dev box.
        assert gui_mod.port_open(1, timeout=0.05) is False


class TestCliWiring:
    def test_gui_flag_is_documented(self):
        import inspect
        from zouwucode import __main__ as entry

        src = inspect.getsource(entry.main)
        assert '"--gui"' in src
        assert "launch_gui" in src and "args.gui" in src


class TestSharedFrontend:
    """GUI and browser must serve the exact same assets (one design)."""

    def test_gui_and_web_share_index(self):
        assert server.page_html() == server.read_static("index.html")[0]
        assert len(server.page_html()) > 1500

    def test_missing_assets_produce_a_clear_page(self, monkeypatch):
        monkeypatch.setattr(server, "read_static", lambda name: None)
        assert server.page_html() == ""


class TestTuiTheme:
    @staticmethod
    def _css():
        from pathlib import Path
        import zouwucode.tui as tui_pkg
        return (Path(tui_pkg.__file__).parent / "styles.tcss").read_text(encoding="utf-8")

    def test_palette_matches_gui(self):
        css = self._css()
        assert "#0a0e16" in css and "#22d3ee" in css, "TUI 应与 GUI 使用同一套主色"
        assert "#a78bfa" in css, "缺少次要色"

    def test_live_selectors_are_styled(self):
        css = self._css()
        for selector in ("Screen", "#header", "#chat-view", "#input-box",
                         "#status-bar", "#footer", "#footer-progress"):
            assert selector in css, f"缺少 {selector} 样式"

    def test_dead_message_classes_removed(self):
        """Chat is rendered as Rich text — .message-* rules styled nothing."""
        css = self._css()
        assert ".message-box" not in css
        assert ".message-assistant" not in css

    def test_focus_state_is_visible(self):
        css = self._css()
        assert "#input-box:focus" in css
        assert "#chat-view:focus" in css
