"""Tests for the context management module."""

import pytest
import tempfile
from pathlib import Path

from zouwucode.context.memory import MemoryManager
from zouwucode.context.topics import TopicManager
from zouwucode.context.transcript import TranscriptManager


class TestMemoryManager:
    """Tests for the MemoryManager class."""

    @pytest.fixture
    def manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield MemoryManager(Path(tmp))

    def test_add_and_get_context(self, manager):
        manager.add_entry("Project uses FastAPI framework")
        manager.add_entry("Database: PostgreSQL")

        context = manager.get_context_block()
        assert "Project uses FastAPI" in context
        assert "Database: PostgreSQL" in context

    def test_clear(self, manager):
        manager.add_entry("Some context")
        manager.clear()
        assert manager.get_context_block() == ""

    def test_add_entry_truncation(self, manager):
        long_entry = "x" * 300
        manager.add_entry(long_entry)
        assert len(manager._entries[0]) <= 200


class TestTopicManager:
    """Tests for the TopicManager class."""

    @pytest.fixture
    def manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield TopicManager(Path(tmp))

    def test_set_and_get_topic(self, manager):
        manager.set_topic("architecture", "The app uses a microservice architecture.")
        content = manager.get_topic("architecture")
        assert content is not None
        assert "microservice" in content

    def test_get_nonexistent_topic(self, manager):
        assert manager.get_topic("nonexistent") is None

    def test_remove_topic(self, manager):
        manager.set_topic("test", "content")
        assert manager.remove_topic("test") is True
        assert manager.remove_topic("nonexistent") is False

    def test_list_topics(self, manager):
        manager.set_topic("topic1", "content1")
        manager.set_topic("topic2", "content2")
        topics = manager.list_topics()
        assert len(topics) == 2


class TestTranscriptManager:
    """Tests for the TranscriptManager class."""

    @pytest.fixture
    def manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield TranscriptManager(Path(tmp))

    def test_start_session_and_append(self, manager):
        manager.start_session("test-session-1")
        manager.append({"role": "user", "content": "Hello"})
        manager.append({"role": "assistant", "content": "Hi there"})

    def test_grep(self, manager):
        manager.start_session("test-session-2")
        manager.append({"role": "user", "content": "What is FastAPI?"})
        manager.append({"role": "assistant", "content": "FastAPI is a web framework"})

        results = manager.grep("FastAPI")
        assert len(results) >= 2

    def test_get_session_list(self, manager):
        manager.start_session("session-a")
        manager.append({"role": "user", "content": "test"})
        sessions = manager.get_session_list()
        assert len(sessions) >= 1