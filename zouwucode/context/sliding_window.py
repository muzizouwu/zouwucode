"""Sliding window context manager — handles ultra-long contexts.

Implements a multi-level context window system:
- Level 0 (Critical): System prompt, frozen prefix — always kept
- Level 1 (Active): Recent turns — kept uncompressed
- Level 2 (Archive): Older turns — compressed/truncated
- Level 3 (Reference): External context — loaded on demand

The window automatically slides as the conversation grows, keeping
the most important context while archiving the rest.
"""

from typing import Optional


class ContextWindow:
    """Manages the sliding context window for conversation history.

    The window is divided into zones:
    - "hot": Always kept (system prompt, current task)
    - "warm": Recent N turns (kept uncompressed)
    - "cold": Older turns (compressed or summarized)
    - "archive": Reference material (loaded on demand, never in hot path)
    """

    def __init__(
        self,
        max_hot_tokens: int = 16000,
        max_warm_tokens: int = 256000,
        max_cold_tokens: int = 776000,
        warm_turns: int = 50,
    ):
        self.max_hot_tokens = max_hot_tokens
        self.max_warm_tokens = max_warm_tokens
        self.max_cold_tokens = max_cold_tokens
        self.warm_turns = warm_turns

        self._hot: list[dict] = []
        self._warm: list[dict] = []
        self._cold: list[dict] = []
        self._archive: dict[str, list[dict]] = {}
        self._compressor = None

    def set_compressor(self, compressor) -> None:
        """Set the dialogue compressor for cold zone compression."""
        self._compressor = compressor

    def add_to_hot(self, messages: list[dict]) -> None:
        """Add messages to the hot zone (always kept)."""
        self._hot.extend(messages)
        self._enforce_token_limit("hot")

    def add_to_warm(self, message: dict) -> None:
        """Add a message to the warm zone (recent turns)."""
        self._warm.append(message)
        self._slide()

    def add_to_archive(self, key: str, messages: list[dict]) -> None:
        """Add reference material to the archive."""
        self._archive[key] = messages

    def get_from_archive(self, key: str) -> Optional[list[dict]]:
        """Retrieve reference material from the archive."""
        return self._archive.get(key)

    def get_context(self) -> dict:
        """Get the full context breakdown."""
        return {
            "hot": list(self._hot),
            "warm": list(self._warm),
            "cold": list(self._cold),
            "archive_keys": list(self._archive.keys()),
        }

    def get_messages(self) -> list[dict]:
        """Get the flattened message list for the LLM."""
        return self._hot + self._warm + self._cold

    def _slide(self) -> None:
        """Slide the window: warm → cold when warm exceeds limits."""
        warm_tokens = self._estimate_tokens(self._warm)

        if len(self._warm) > self.warm_turns * 2 or warm_tokens > self.max_warm_tokens:
            # Move oldest warm messages to cold
            overflow = self._warm[:len(self._warm) // 2]
            self._warm = self._warm[len(self._warm) // 2:]

            # Compress the overflow before adding to cold
            if self._compressor and len(overflow) > 5:
                overflow = self._compressor._compress_block(overflow)

            self._cold.extend(overflow)
            self._enforce_token_limit("cold")

    def _enforce_token_limit(self, zone: str) -> None:
        """Enforce token limits on a zone by dropping oldest content."""
        target = getattr(self, f"_{zone}", [])
        max_tokens = getattr(self, f"max_{zone}_tokens", 0)

        while self._estimate_tokens(target) > max_tokens and len(target) > 2:
            # Drop the oldest message (keep at least 2)
            target.pop(0)

    def _estimate_tokens(self, messages: list[dict]) -> int:
        """Rough token estimation."""
        total = 0
        for msg in messages:
            total += len(msg.get("content", "")) // 2 + 4
        return total

    def get_stats(self) -> dict:
        """Get context window statistics."""
        return {
            "hot_messages": len(self._hot),
            "warm_messages": len(self._warm),
            "cold_messages": len(self._cold),
            "archive_keys": len(self._archive),
            "hot_tokens": self._estimate_tokens(self._hot),
            "warm_tokens": self._estimate_tokens(self._warm),
            "cold_tokens": self._estimate_tokens(self._cold),
            "total_tokens": self._estimate_tokens(self.get_messages()),
        }

    def reset(self) -> None:
        """Reset the entire context window."""
        self._hot.clear()
        self._warm.clear()
        self._cold.clear()
        self._archive.clear()