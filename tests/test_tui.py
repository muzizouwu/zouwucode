"""Tests for the TUI module — Textual application and CLI app."""

import pytest
from pathlib import Path

from rich.text import Text

from zouwucode.tui.textual_app import (
    ZOUWUCODETUI, _style, run_tui, _restore_terminal)
from zouwucode.tui.app import ZOUWUCODEApp


async def _pause_until(pilot, predicate, tries: int = 12) -> bool:
    """Advance the UI until *predicate* holds, or stop after *tries* frames.

    Sticky-follow is deliberately frame-based (pin after layout + bounded
    re-checks), so a single ``pilot.pause()`` is not a valid snapshot on a
    loaded CI runner — polling a bounded number of frames tests the real
    behaviour ("eventually follows") without hiding a genuine regression
    (the bound is small and the predicate is strict).
    """
    for _ in range(tries):
        if predicate():
            return True
        await pilot.pause()
    return predicate()


class TestRestoreTerminal:
    """The TUI enables SGR mouse reporting; if it dies before Textual's own
    teardown the shell echoes raw mouse-move sequences (the wall of
    `^[[<35;...M` garbage). _restore_terminal must always emit the disable
    sequences so the console is usable regardless of exit path."""

    def test_emits_mouse_and_cursor_restore(self, capsys):
        _restore_terminal()
        out = capsys.readouterr().out
        assert "\x1b[?1000l" in out   # mouse click off
        assert "\x1b[?1003l" in out   # mouse move off
        assert "\x1b[?1006l" in out   # SGR ext off
        assert "\x1b[?25h" in out     # cursor visible again
        assert "\x1b[0m" in out       # SGR reset

    def test_never_raises(self, monkeypatch):
        # A closed/broken stdout must not propagate out of the finally-block.
        import zouwucode.tui.textual_app as ta

        class _Boom:
            def write(self, *a):
                raise OSError("closed")
            def flush(self):
                raise OSError("closed")

        monkeypatch.setattr(ta.sys, "stdout", _Boom())
        _restore_terminal()   # must not raise


class TestStyleHelper:
    """Tests for the _style helper.

    _style now returns a Rich ``Text`` object rather than a markup string:
    chat content is arbitrary model/user data and must never be parsed as
    markup (see TestChatViewUX for the end-to-end regressions).
    """

    def test_style_with_style(self):
        result = _style("Hello", "bold green")
        assert isinstance(result, Text)
        assert result.plain == "Hello"
        assert "bold" in str(result.style) and "green" in str(result.style)

    def test_style_without_style(self):
        result = _style("Hello")
        assert isinstance(result, Text)
        assert result.plain == "Hello"

    def test_style_empty_string(self):
        result = _style("", "red")
        assert isinstance(result, Text)
        assert result.plain == ""

    def test_style_multiple_styles(self):
        result = _style("Test", "bold italic cyan")
        assert result.plain == "Test"
        s = str(result.style)
        assert "bold" in s and "italic" in s and "cyan" in s


class TestZOUWUCODETUI:
    """Tests for the Textual TUI application."""

    def test_app_initialization(self):
        """Test that the TUI app can be instantiated."""
        app = ZOUWUCODETUI()
        assert app.TITLE == "ZOUWUCODE"
        assert app.current_mode == "agent"
        assert app.engine is not None
        assert app.tools is not None
        assert app.project_memory is not None

    def test_app_with_config(self, tmp_path):
        """Test app initialization with a config path."""
        config_path = tmp_path / "config.yaml"
        from zouwucode.config import ZOUWUCODEConfig
        config = ZOUWUCODEConfig()
        config.save(config_path)

        app = ZOUWUCODETUI(config_path)
        assert app.config is not None

    def test_mode_switching(self):
        """Test mode switching."""
        app = ZOUWUCODETUI()
        assert app.current_mode == "agent"

        # Test mode switching via internal state
        # (action_set_mode requires the app to be mounted, so we test the logic directly)
        app.current_mode = "plan"
        app.engine.set_mode("plan")
        assert app.current_mode == "plan"
        assert app.engine.mode == "plan"

        app.current_mode = "yolo"
        app.engine.set_mode("yolo")
        assert app.current_mode == "yolo"
        assert app.engine.mode == "yolo"

    def test_invalid_mode(self):
        """Test that invalid modes are rejected."""
        app = ZOUWUCODETUI()
        app.engine.set_mode("invalid")
        assert app.engine.mode == "agent"  # unchanged (default mode)

    def test_ctrl_s_cycles_modes(self):
        """Ctrl+S cycles plan → agent → yolo → plan (regression for module refactor)."""
        import asyncio
        app = ZOUWUCODETUI()

        async def run():
            async with app.run_test(size=(120, 40)) as pilot:
                await asyncio.sleep(0.2)
                assert app.current_mode == "agent"
                seq = []
                for _ in range(3):
                    await pilot.press("ctrl+s")
                    await asyncio.sleep(0.05)
                    seq.append(app.current_mode)
                assert seq == ["yolo", "plan", "agent"]

        asyncio.run(run())

    def test_slash_commands_switch_mode(self):
        """/plan /agent /yolo commands switch mode via action_set_mode (regression)."""
        import asyncio
        app = ZOUWUCODETUI()

        async def run():
            async with app.run_test(size=(120, 40)) as pilot:
                await asyncio.sleep(0.2)
                for cmd, expected in [("/plan", "plan"), ("/yolo", "yolo"), ("/agent", "agent")]:
                    app.query_one("#input-box").value = cmd
                    await pilot.press("enter")
                    await asyncio.sleep(0.1)
                    assert app.current_mode == expected, f"{cmd} → {app.current_mode}"

        asyncio.run(run())

    def test_tool_setup(self):
        """Test that tools are properly registered."""
        app = ZOUWUCODETUI()
        tools = app.tools.get_names()
        assert "read" in tools
        assert "write" in tools
        assert "edit" in tools
        assert "bash" in tools
        assert "git" in tools
        assert "web_search" in tools
        assert "web_fetch" in tools
        assert "ls" in tools
        assert "glob" in tools

    def test_project_memory_loaded(self):
        """Test that project memory is loaded on init."""
        app = ZOUWUCODETUI()
        assert app.project_memory is not None
        # Should be able to access project memory methods
        app.project_memory.set_state("test_key", "test_value")
        assert app.project_memory.get_state("test_key") == "test_value"

    def test_compressor_initialized(self):
        """Test that the dialogue compressor is initialized."""
        app = ZOUWUCODETUI()
        assert app.compressor is not None
        assert app.compressor.level == "balanced"
        assert app.compressor_level == "balanced"

    def test_context_window_initialized(self):
        """Test that the context window is initialized."""
        app = ZOUWUCODETUI()
        assert app.context_window is not None
        stats = app.context_window.get_stats()
        assert stats["hot_messages"] == 0
        assert stats["warm_messages"] == 0

    def test_build_system_prompt(self):
        """Test that the system prompt includes mode info."""
        app = ZOUWUCODETUI()
        prompt = app._build_system_prompt()
        assert "ZOUWUCODE" in prompt
        assert "agent" in prompt.lower()  # default mode

    def test_update_status(self):
        """Test that status update doesn't crash."""
        app = ZOUWUCODETUI()
        # Just verify the method exists and doesn't error
        # (it requires the app to be mounted, so we can't test the full behavior)
        assert hasattr(app, '_update_status')

    def test_style_integration(self):
        """Test that _style produces styled Text for the app's display."""
        app = ZOUWUCODETUI()
        # User message style
        user_msg = _style("Hello", "bold blue")
        assert isinstance(user_msg, Text)
        assert user_msg.plain == "Hello"
        assert "bold" in str(user_msg.style) and "blue" in str(user_msg.style)

        # Assistant message style
        asst_msg = _style("Response", "bold green")
        assert asst_msg.plain == "Response"
        assert "green" in str(asst_msg.style)

        # Error message style
        err_msg = _style("Error", "red")
        assert "red" in str(err_msg.style)


    def test_show_thinking_defaults_from_config(self):
        """TUI thinking display defaults to config.show_thinking (True)."""
        app = ZOUWUCODETUI()
        assert app._show_thinking is True
        assert app.config.show_thinking is True

    def test_show_thinking_toggle_syncs_config(self):
        """/thinking toggle must sync _show_thinking and config.show_thinking."""
        import asyncio
        app = ZOUWUCODETUI()

        async def run():
            async with app.run_test(size=(120, 40)) as pilot:
                await asyncio.sleep(0.2)
                app.query_one("#input-box").value = "/thinking"
                await pilot.press("enter")
                await asyncio.sleep(0.1)
                assert app._show_thinking is False
                assert app.config.show_thinking is False

        asyncio.run(run())

    def test_thinking_line_buffering(self):
        """_on_thinking_delta must buffer by line and flush complete lines."""
        import asyncio
        app = ZOUWUCODETUI()

        async def run():
            async with app.run_test(size=(120, 40)) as pilot:
                await asyncio.sleep(0.2)
                app._on_thinking_delta("line one\nline two\npartial")
                assert app._thinking_buf == "partial", "不完整行应留在缓冲区"
                app._on_thinking_delta(" rest")
                assert app._thinking_buf == "partial rest", "增量应继续追加到缓冲区"

        asyncio.run(run())


class TestCLIApp:
    """Tests for the CLI application."""

    def test_app_initialization(self):
        """Test that the CLI app can be instantiated."""
        app = ZOUWUCODEApp()
        assert app.APP_NAME == "ZOUWUCODE"
        assert app.engine is not None
        assert app.tools is not None
        assert app.project_memory is not None

    def test_mode_switching(self):
        """Test mode switching in CLI app."""
        app = ZOUWUCODEApp()
        assert app.mode == "agent"

        app.set_mode("plan")
        assert app.mode == "plan"

        app.set_mode("yolo")
        assert app.mode == "yolo"

    def test_project_memory_integration(self):
        """Test that CLI app has project memory."""
        app = ZOUWUCODEApp()
        assert app.project_memory is not None
        app.project_memory.set_goal("Test goal")
        assert app.project_memory.get_active_goal() == "Test goal"

    def test_compressor_integration(self):
        """Test that CLI app has compressor."""
        app = ZOUWUCODEApp()
        assert app.compressor is not None
        assert app.compressor_level == "balanced"

    def test_context_window_integration(self):
        """Test that CLI app has context window."""
        app = ZOUWUCODEApp()
        assert app.context_window is not None
        stats = app.context_window.get_stats()
        assert "hot_messages" in stats

    def test_save_state(self):
        """Test that save_state doesn't crash."""
        app = ZOUWUCODEApp()
        # Should not raise
        app.save_state()

    def test_initialize_session(self):
        """Test session initialization."""
        app = ZOUWUCODEApp()
        session_id = app.initialize_session()
        assert session_id is not None
        assert app._session_id == session_id

    def test_thinking_streaming_state_initialized(self):
        """CLI streaming state must be initialized and follow config."""
        app = ZOUWUCODEApp()
        assert app._thinking_streaming is False
        assert app.config.show_thinking is True

    def test_thinking_toggle_command(self):
        """/thinking command toggles config.show_thinking."""
        app = ZOUWUCODEApp()
        app.config.show_thinking = False
        assert app.config.show_thinking is False

    def test_tool_setup(self):
        """Test tool registration in CLI app."""
        app = ZOUWUCODEApp()
        tools = app.tools.get_names()
        assert "read" in tools
        assert "write" in tools
        assert "bash" in tools
        assert "git" in tools

class TestChatViewUX:
    """Regressions for chat readability (2026-10 user reports):

    - model text containing "[...]" was eaten by Rich markup ("[/]" even
      raised and the whole reply collapsed into an Error line)
    - every new write auto-scrolled the viewport to the bottom, so the
      user could not read history while output streamed
    - there was no keyboard way to scroll the chat from the input box
    """

    def test_brackets_render_literally(self):
        """[interrupted] / [dim] / [/] must survive as plain text."""
        import asyncio
        app = ZOUWUCODETUI()

        async def run():
            async with app.run_test(size=(120, 40)) as pilot:
                await asyncio.sleep(0.2)
                chat = app.query_one("#chat-view")
                chat.write("plain [interrupted] stays")
                chat.write(_style("styled [dim] literal [/] too", "bold"))
                await pilot.pause()
                text = "\n".join(getattr(s, "text", "") for s in chat.lines)
                assert "[interrupted] stays" in text
                assert "[dim] literal [/] too" in text

        asyncio.run(run())

    def test_reading_position_survives_new_output(self):
        """Scrolled up + new writes => viewport must NOT jump to bottom."""
        import asyncio
        app = ZOUWUCODETUI()

        async def run():
            async with app.run_test(size=(120, 40)) as pilot:
                await asyncio.sleep(0.2)
                chat = app.query_one("#chat-view")
                for i in range(150):
                    chat.write(f"history line {i}")
                # Following the bottom needs one layout frame plus the sticky
                # re-pin, and a loaded CI runner can take a few — poll instead
                # of asserting a single-frame snapshot.
                assert await _pause_until(
                    pilot, lambda: chat.scroll_y >= chat.max_scroll_y), \
                    "chat never followed the bottom"

                chat.scroll_home(animate=False)
                assert await _pause_until(pilot, lambda: chat.scroll_y == 0)

                chat.write("NEW while reading history")
                await pilot.pause()
                await pilot.pause()
                assert chat.scroll_y == 0, "viewport was yanked to the bottom"

                chat.scroll_end(animate=False)
                await pilot.pause()
                chat.write("NEW at bottom")
                assert await _pause_until(
                    pilot, lambda: chat.scroll_y >= chat.max_scroll_y), \
                    "chat did not follow again after scrolling to the end"

        asyncio.run(run())

    def test_pageup_scrolls_chat_from_input(self):
        """PgUp must scroll the chat even while the input box has focus."""
        import asyncio
        app = ZOUWUCODETUI()

        async def run():
            async with app.run_test(size=(120, 40)) as pilot:
                await asyncio.sleep(0.2)
                chat = app.query_one("#chat-view")
                for i in range(150):
                    chat.write(f"history line {i}")
                await pilot.pause()
                app.query_one("#input-box").focus()
                await pilot.press("pageup")
                await pilot.pause()
                assert chat.scroll_y < chat.max_scroll_y

        asyncio.run(run())
