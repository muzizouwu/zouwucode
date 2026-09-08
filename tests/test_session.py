"""Tests for the session management module."""

import pytest
import tempfile
from pathlib import Path

from zouwucode.session.manager import SessionManager
from zouwucode.config import ZOUWUCODEConfig


class TestSessionManager:
    """Tests for the SessionManager class."""

    @pytest.fixture
    def manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = ZOUWUCODEConfig()
            config.data_dir = str(tmp)
            yield SessionManager(config)

    def test_start_session(self, manager):
        session_id = manager.start_session()
        assert session_id is not None
        assert manager.current_session_id == session_id

    def test_log_turn(self, manager):
        manager.start_session("test-session")
        manager.log_turn({"role": "user", "content": "hello"})
        manager.log_turn({"role": "assistant", "content": "world"})

    def test_list_sessions(self, manager):
        manager.start_session("session-1")
        manager.log_turn({"role": "user", "content": "msg1"})

        manager.start_session("session-2")
        manager.log_turn({"role": "user", "content": "msg2"})

        sessions = manager.list_sessions()
        assert len(sessions) == 2

    def test_delete_session(self, manager):
        manager.start_session("delete-me")
        manager.log_turn({"role": "user", "content": "test"})
        assert manager.delete_session("delete-me") is True
        assert manager.delete_session("nonexistent") is False

    def test_get_session(self, manager):
        manager.start_session("get-me")
        manager.log_turn({"role": "user", "content": "test"})
        data = manager.get_session("get-me")
        assert data is not None
        assert data["session_id"] == "get-me"