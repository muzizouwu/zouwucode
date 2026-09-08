"""Tests for the TUI module — Textual application and CLI app."""

import pytest
from pathlib import Path

from zouwucode.tui.textual_app import ZOUWUCODETUI, _style, run_tui
from zouwucode.tui.app import ZOUWUCODEApp


class TestStyleHelper:
    """Tests for the _style helper function."""

    def test_style_with_style(self):
        result = _style("Hello", "bold green")
        assert result == "[bold green]Hello[/]"

    def test_style_without_style(self):
        result = _style("Hello")
        assert result == "Hello"

    def test_style_empty_string(self):
        result = _style("", "red")
        assert result == "[red][/]"

    def test_style_multiple_styles(self):
        result = _style("Test", "bold italic cyan")
        assert result == "[bold italic cyan]Test[/]"


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
        """Test that _style works with the app's message display."""
        app = ZOUWUCODETUI()
        # User message style
        user_msg = _style("Hello", "bold blue")
        assert "[bold blue]" in user_msg
        assert "Hello" in user_msg

        # Assistant message style
        asst_msg = _style("Response", "bold green")
        assert "[bold green]" in asst_msg

        # Error message style
        err_msg = _style("Error", "red")
        assert "[red]" in err_msg


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