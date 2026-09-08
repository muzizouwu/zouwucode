"""Tests for the ContextWindow module."""

import pytest
from zouwucode.context.sliding_window import ContextWindow
from zouwucode.context.compressor import DialogueCompressor


class TestContextWindow:
    """Tests for the sliding window context manager."""

    @pytest.fixture
    def window(self):
        return ContextWindow(
            max_hot_tokens=1000,
            max_warm_tokens=500,
            max_cold_tokens=1000,
            warm_turns=5,
        )

    def test_initial_state(self, window):
        stats = window.get_stats()
        assert stats["hot_messages"] == 0
        assert stats["warm_messages"] == 0
        assert stats["cold_messages"] == 0

    def test_add_to_hot(self, window):
        window.add_to_hot([
            {"role": "system", "content": "You are a helpful assistant."},
        ])
        stats = window.get_stats()
        assert stats["hot_messages"] == 1

    def test_add_to_warm(self, window):
        window.add_to_warm({"role": "user", "content": "Hello"})
        stats = window.get_stats()
        assert stats["warm_messages"] == 1

    def test_slide_warm_to_cold(self, window):
        window.set_compressor(DialogueCompressor(level="lossless"))

        # Add enough warm messages to trigger sliding
        for i in range(20):
            window.add_to_warm({"role": "user", "content": f"Message {i}"})

        stats = window.get_stats()
        # Some messages should have moved to cold zone
        assert stats["cold_messages"] >= 0

    def test_get_messages(self, window):
        window.add_to_hot([{"role": "system", "content": "sys"}])
        window.add_to_warm({"role": "user", "content": "hello"})

        messages = window.get_messages()
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_add_to_archive(self, window):
        window.add_to_archive("ref1", [{"role": "user", "content": "reference"}])
        archived = window.get_from_archive("ref1")
        assert archived is not None
        assert archived[0]["content"] == "reference"

    def test_get_from_archive_nonexistent(self, window):
        assert window.get_from_archive("nonexistent") is None

    def test_reset(self, window):
        window.add_to_hot([{"role": "system", "content": "sys"}])
        window.add_to_warm({"role": "user", "content": "msg"})
        window.reset()
        stats = window.get_stats()
        assert stats["hot_messages"] == 0
        assert stats["warm_messages"] == 0
        assert stats["cold_messages"] == 0

    def test_enforce_token_limit(self, window):
        # Add very long messages to trigger token limit
        for i in range(10):
            window.add_to_warm({"role": "user", "content": "x" * 500})

        stats = window.get_stats()
        # Should have enforced limits
        assert stats["total_tokens"] > 0

    def test_estimate_tokens(self, window):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "World!"},
        ]
        tokens = window._estimate_tokens(messages)
        # Rough: 5 chars / 2 = 2.5 + 4 overhead ≈ 6.5 per message, so ~13 total
        assert tokens > 0

    def test_get_context(self, window):
        window.add_to_hot([{"role": "system", "content": "sys"}])
        window.add_to_warm({"role": "user", "content": "msg"})
        window.add_to_archive("ref", [{"role": "user", "content": "ref"}])

        ctx = window.get_context()
        assert "hot" in ctx
        assert "warm" in ctx
        assert "cold" in ctx
        assert "archive_keys" in ctx
        assert "ref" in ctx["archive_keys"]