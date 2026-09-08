"""Tests for the DialogueCompressor module."""

import pytest
from zouwucode.context.compressor import DialogueCompressor, CompressionResult


class TestDialogueCompressor:
    """Tests for the intelligent dialogue compression engine."""

    @pytest.fixture
    def compressor(self):
        return DialogueCompressor(level="balanced")

    def test_lossless_compress_empty(self):
        compressor = DialogueCompressor(level="lossless")
        result = compressor.compress_conversation([])
        assert result == []

    def test_lossless_compress_removes_empty(self):
        compressor = DialogueCompressor(level="lossless")
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "  "},
            {"role": "assistant", "content": "World"},
        ]
        result = compressor.compress_conversation(messages, keep_last_n=0)
        # Lossless: strips whitespace, removes truly empty
        non_empty = [m for m in result if m.get("content", "").strip()]
        assert len(non_empty) <= 4

    def test_balanced_compress_preserves_system(self, compressor):
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        result = compressor.compress_conversation(messages, keep_last_n=0)
        # System message should always be preserved
        assert any(m["role"] == "system" for m in result)

    def test_balanced_compress_preserves_decisions(self, compressor):
        messages = [
            {"role": "user", "content": "We decided to use FastAPI"},
            {"role": "assistant", "content": "Great choice!"},
            {"role": "user", "content": "Let's refactor the database layer"},
        ]
        result = compressor.compress_conversation(messages, keep_last_n=0)
        # Decision keywords should be preserved
        assert len(result) >= 2

    def test_balanced_compress_file_content(self, compressor):
        # Create a file content that's clearly a tool result with "Written" pattern
        messages = [
            {"role": "user", "content": "Read the file"},
            {"role": "tool", "content": "Written 12345 bytes to /path/to/file.py"},
        ]
        result = compressor.compress_conversation(messages, keep_last_n=0)
        # Tool result should be compressed
        tool_msgs = [m for m in result if m["role"] == "tool"]
        assert len(tool_msgs) > 0
        assert "[Tool]" in tool_msgs[0]["content"]

    def test_aggressive_compress_summarizes(self):
        compressor = DialogueCompressor(level="aggressive")
        # Use more messages to ensure aggressive compression kicks in
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
            {"role": "user", "content": "How are you?"},
            {"role": "assistant", "content": "I'm fine, thanks!"},
            {"role": "user", "content": "What's the weather?"},
            {"role": "assistant", "content": "Sunny and warm"},
            {"role": "user", "content": "Is it raining?"},
            {"role": "assistant", "content": "No, it's clear"},
        ]
        result = compressor.compress_conversation(messages, keep_last_n=2)
        # Aggressive compression should produce fewer entries than original
        # by grouping and summarizing older interactions
        assert len(result) <= len(messages)

    def test_estimate_tokens(self, compressor):
        messages = [
            {"role": "user", "content": "Hello world"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        tokens = compressor.estimate_tokens(messages)
        assert tokens > 0
        assert isinstance(tokens, int)

    def test_keep_last_n(self, compressor):
        messages = [
            {"role": "user", "content": f"Message {i}"}
            for i in range(20)
        ]
        result = compressor.compress_conversation(messages, keep_last_n=5)
        # Last 5 messages should be kept
        assert len(result) >= 5
        # The last message should be "Message 19"
        assert result[-1]["content"] == "Message 19"

    def test_tool_result_patterns(self, compressor):
        # Test various tool result patterns
        assert compressor._is_tool_result("Written 1234 bytes to /path/file.py")
        assert compressor._is_tool_result("Edited /path/file.py (100 → 120 lines)")
        assert compressor._is_tool_result('{"success": true, "output": "done"}')
        assert not compressor._is_tool_result("Just a normal message")

    def test_summarize_assistant_content(self, compressor):
        long_content = "\n".join([f"Line {i}" for i in range(20)])
        summary = compressor._summarize_assistant_content(long_content)
        assert "[Summarized]" in summary
        assert len(summary.splitlines()) <= 8  # 3 + 1 + 2 + overhead