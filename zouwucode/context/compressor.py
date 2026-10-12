"""Auto-dialogue compression — intelligently compress conversation history.

The compression algorithm:
1. Identifies and removes redundant/repeated information
2. Summarizes tool call sequences into compact descriptions
3. Preserves key decisions, code changes, and error states
4. Classifies messages into: keep-full, keep-summary, or discard
5. Runs automatically when context approaches token limits
"""
import logging

logger = logging.getLogger("zouwucode.context.compressor")


import re

from ..tokens import estimate_messages


# ── Patterns for identifying compressible content ────────────────────────────

# Tool result patterns that are safe to compress
TOOL_RESULT_PATTERNS = [
    r"^Written \d+ bytes to",
    r"^Edited .* \(\d+ → \d+ lines\)",
    r"^\d+→",
    r"^Exit code:",
    r"^\{.*\"success\":.*true",
]

# Pattern for file content (can be very long)
FILE_CONTENT_PATTERN = re.compile(r"^\d{1,6}→.*$", re.MULTILINE)

# Decision keywords that should be preserved
DECISION_KEYWORDS = [
    "decided to", "chose to", "we should", "let's use",
    "the best approach", "prefer", "selected", "changing to",
    "refactored", "renamed", "migrated", "upgraded",
]


class CompressionResult:
    """Result of compressing a conversation turn."""

    def __init__(
        self,
        compressed: str,
        original_length: int,
        compressed_length: int,
        preserved_decisions: list[str],
        preserved_errors: list[str],
    ):
        self.compressed = compressed
        self.original_length = original_length
        self.compressed_length = compressed_length
        self.preserved_decisions = preserved_decisions
        self.preserved_errors = preserved_errors
        self.ratio = compressed_length / max(original_length, 1)

    @property
    def summary(self) -> str:
        return f"Compressed {self.original_length}→{self.compressed_length} chars ({self.ratio:.0%}), decisions: {len(self.preserved_decisions)}, errors: {len(self.preserved_errors)}"


class DialogueCompressor:
    """Intelligent conversation compression engine.

    Compression levels:
    - "lossless": Only removes truly redundant whitespace and empty turns
    - "balanced" (default): Compresses tool results, keeps decision content
    - "aggressive": Summarizes all but the most recent messages
    """

    def __init__(self, level: str = "balanced"):
        self.level = level

    def compress_conversation(
        self,
        messages: list[dict],
        keep_last_n: int = 50,
        max_context_tokens: int = 1048576,
    ) -> list[dict]:
        """Compress a conversation to fit within token limits.

        Args:
            messages: Full conversation history
            keep_last_n: Always keep the last N messages uncompressed
            max_context_tokens: Target maximum token count

        Returns:
            Compressed message list
        """
        if not messages:
            return messages

        # Always keep the last N messages
        # Note: Python [:-0] returns empty, so handle keep_last_n=0 explicitly
        msg_count = len(messages)
        if keep_last_n <= 0:
            head = list(messages)
            tail = []
        elif msg_count > keep_last_n:
            head = messages[:-keep_last_n]
            tail = messages[-keep_last_n:]
        else:
            head = []
            tail = messages

        if not head:
            return messages

        # Compress the head (older messages)
        compressed = self._compress_block(head)
        result = compressed + tail

        # If still too long, apply more aggressive compression
        estimated_tokens = estimate_messages(result)
        if estimated_tokens > max_context_tokens and self.level != "lossless":
            # Keep even fewer messages uncompressed — but ONLY recurse when
            # that changes the parameters. min(0, 5) == 0 and min(5, 5) == 5
            # made the old version call itself with identical arguments and
            # spin until RecursionError whenever the budget was unreachable.
            tighter = 5 if keep_last_n <= 0 else min(keep_last_n, 5)
            if tighter != keep_last_n:
                return self.compress_conversation(
                    messages,
                    keep_last_n=tighter,
                    max_context_tokens=max_context_tokens,
                )

        return result

    def _compress_block(self, messages: list[dict]) -> list[dict]:
        """Compress a block of messages according to the current level."""
        if self.level == "lossless":
            return self._lossless_compress(messages)
        elif self.level == "aggressive":
            return self._aggressive_compress(messages)
        else:
            return self._balanced_compress(messages)

    def _lossless_compress(self, messages: list[dict]) -> list[dict]:
        """Lossless compression: remove whitespace only."""
        result = []
        for msg in messages:
            content = msg.get("content", "").strip()
            # A message with tool_calls may legitimately have empty content;
            # dropping it strands its tool results (orphaned tool_call_id).
            if content or msg.get("tool_calls"):
                result.append({**msg, "content": content})
        return result

    def _balanced_compress(self, messages: list[dict]) -> list[dict]:
        """Balanced compression: compress tool results, keep decisions."""
        result = []
        preserved_decisions = []
        preserved_errors = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            # Keep tool_calls carriers (empty content is valid for them) —
            # dropping them orphans the paired tool results.
            if not content.strip() and not msg.get("tool_calls"):
                continue

            # Always preserve system messages
            if role == "system":
                result.append(msg)
                continue

            # Preserve messages containing decisions
            if any(kw in content.lower() for kw in DECISION_KEYWORDS):
                preserved_decisions.append(content[:200])
                result.append(msg)
                continue

            # Preserve error messages
            if "error" in content.lower() and len(content) < 500:
                preserved_errors.append(content[:200])
                result.append(msg)
                continue

            # Compress tool responses
            if role == "tool":
                # Check if the content matches tool result patterns
                if self._is_tool_result(content):
                    summary = self._summarize_tool_result(content)
                    if summary:
                        result.append({**msg, "content": summary})
                        continue

            # Compress assistant messages with long file content
            if role == "assistant" and len(content) > 500:
                summary = self._summarize_assistant_content(content)
                result.append({**msg, "content": summary})
                continue

            result.append(msg)

        return result

    def _aggressive_compress(self, messages: list[dict]) -> list[dict]:
        """Aggressive compression: summarize entire blocks of messages."""
        # Group messages into interaction groups (user+assistant+tool)
        groups = self._group_interactions(messages)

        result = []
        for i, group in enumerate(groups):
            if len(groups) - i <= 3:  # Keep last 3 groups uncompressed
                result.extend(group)
                continue

            # Summarize the group
            summary = self._summarize_interaction_group(group)
            if summary:
                # NOT role "system": most providers require system first, so a
                # mid-conversation system message is rejected or ignored. A
                # user-role note is legal in-place and reads as history.
                result.append(
                    {"role": "user", "content": f"[Compressed earlier history] {summary}"})

        return result

    def _is_tool_result(self, content: str) -> bool:
        """Check if content is a compressible tool result."""
        for pattern in TOOL_RESULT_PATTERNS:
            if re.match(pattern, content):
                return True
        # File content (many lines with line numbers)
        file_lines = FILE_CONTENT_PATTERN.findall(content)
        if len(file_lines) > 20:
            return True
        return False

    def _summarize_tool_result(self, content: str) -> str:
        """Summarize a tool result into a compact form."""
        if content.startswith("Written"):
            # "Written 1234 bytes to path/to/file"
            return f"[Tool] {content}"
        if content.startswith("Edited"):
            # "Edited path/to/file (100 → 120 lines)"
            return f"[Tool] {content}"
        if content.startswith("{"):
            # JSON result
            try:
                import json
                data = json.loads(content)
                if data.get("success"):
                    return f"[Tool] Success: {data.get('output', '')[:100]}"
                else:
                    return f"[Tool] Error: {data.get('error', '')[:100]}"
            except Exception as _exc:
                logger.debug("swallowed %s: %s", type(_exc).__name__, _exc)
        # File content lines — keep head + tail so the model still sees the
        # shape of the file (a bare "N lines (compressed)" loses everything).
        lines = content.splitlines()
        if len(lines) > 20:
            head, tail = lines[:8], lines[-6:]
            hidden = len(lines) - len(head) - len(tail)
            return ("[File Content] %d lines\n%s\n... (%d more lines) ...\n%s"
                    % (len(lines), "\n".join(head), hidden, "\n".join(tail)))
        return content[:200]

    def _summarize_assistant_content(self, content: str) -> str:
        """Summarize a long assistant response."""
        lines = content.splitlines()
        if len(lines) <= 5:
            return content

        # Keep first 3 and last 2 lines
        summary = "\n".join(lines[:3] + ["..."] + lines[-2:])
        return f"[Summarized] ({len(lines)} lines → 6 lines)\n{summary}"

    def _group_interactions(self, messages: list[dict]) -> list[list[dict]]:
        """Group messages into user/assistant/tool interaction groups."""
        groups = []
        current = []
        for msg in messages:
            role = msg.get("role", "")
            if role == "user" and current:
                groups.append(current)
                current = []
            current.append(msg)
        if current:
            groups.append(current)
        return groups

    def _summarize_interaction_group(self, group: list[dict]) -> str:
        """Summarize a group of interactions into a single sentence."""
        parts = []
        for msg in group:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                parts.append(f"User: {content[:100]}")
            elif role == "assistant" and "tool_calls" in msg:
                parts.append("Assistant called tools")
            elif role == "assistant":
                parts.append(f"Assistant: {content[:100]}")
            elif role == "tool":
                parts.append("Tool executed")
        return " | ".join(parts)

    def estimate_tokens(self, messages: list[dict]) -> int:
        """Token estimation via the shared estimator (was len//2)."""
        return estimate_messages(messages)