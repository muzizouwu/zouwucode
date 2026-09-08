"""Prefix-cache management — the core of the cache-first strategy.

This module implements the cache-tracking and statistics that mirror
Reasonix's approach: keep the prefix byte-identical so DeepSeek's
server-side cache stays hot.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CacheStats:
    """Real-time cache performance statistics."""

    total_requests: int = 0
    cache_hits: int = 0
    total_prompt_tokens: int = 0
    cached_prompt_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    window_hits: deque = field(default_factory=lambda: deque(maxlen=50))
    window_misses: deque = field(default_factory=lambda: deque(maxlen=50))
    _start_time: float = field(default_factory=time.time)

    @property
    def hit_rate(self) -> float:
        """Overall cache hit rate (0.0 - 1.0)."""
        if self.total_requests == 0:
            return 0.0
        return self.cache_hits / self.total_requests

    @property
    def window_hit_rate(self) -> float:
        """Recent window cache hit rate."""
        total = len(self.window_hits) + len(self.window_misses)
        if total == 0:
            return 0.0
        return len(self.window_hits) / total

    @property
    def cached_token_ratio(self) -> float:
        """Ratio of prompt tokens served from cache."""
        if self.total_prompt_tokens == 0:
            return 0.0
        return self.cached_prompt_tokens / self.total_prompt_tokens

    @property
    def uptime(self) -> float:
        """Session uptime in seconds."""
        return time.time() - self._start_time

    @property
    def estimated_savings(self) -> float:
        """Estimated cost savings from caching.

        Assumes cached tokens bill at ~1/5 of uncached rate.
        """
        if self.total_prompt_tokens == 0:
            return 0.0
        uncached_cost = self.total_prompt_tokens * 0.14 / 1_000_000  # V4-Flash uncached
        cached_cost = self.cached_prompt_tokens * 0.028 / 1_000_000  # V4-Flash cached
        return (uncached_cost - cached_cost) * 0.8  # approximate

    def record_turn(self, cache_hit: bool, usage: dict) -> None:
        """Record a single turn's cache performance."""
        self.total_requests += 1
        prompt_tokens = usage.get("prompt_tokens", 0)
        cached_tokens = usage.get("prompt_cache_hit_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        if cache_hit:
            self.cache_hits += 1
            self.window_hits.append(True)
        else:
            self.window_misses.append(True)

        self.total_prompt_tokens += prompt_tokens
        self.cached_prompt_tokens += cached_tokens
        self.total_output_tokens += output_tokens

        # Estimate cost: DeepSeek V4-Flash pricing
        uncached_cost = (prompt_tokens - cached_tokens) * 0.14 / 1_000_000
        cached_cost = cached_tokens * 0.028 / 1_000_000
        output_cost = output_tokens * 0.28 / 1_000_000
        self.total_cost += uncached_cost + cached_cost + output_cost


class PrefixCache:
    """Manages the deterministic prefix for cache stability.

    The key insight (from Reasonix): to maintain >90% cache hit rate,
    the conversation history MUST be append-only. No reordering, no
    compression, no insertion of dynamic content in the prefix.
    """

    def __init__(self, max_prefix_tokens: int = 128_000):
        self.max_prefix_tokens = max_prefix_tokens
        self._frozen_messages: list[dict] = []
        self._frozen = False

    def freeze(self, messages: list[dict]) -> None:
        """Freeze the initial message prefix once at session start."""
        if not self._frozen:
            self._frozen_messages = list(messages)
            self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def append(self, message: dict) -> None:
        """Append a message — NEVER prepend or reorder."""
        self._frozen_messages.append(message)

    def get_prefix(self) -> list[dict]:
        """Return the full append-only message list."""
        return list(self._frozen_messages)

    def get_prefix_length(self) -> int:
        """Rough token count of the current prefix."""
        total = 0
        for msg in self._frozen_messages:
            total += len(msg.get("content", "")) // 2  # rough est: 2 chars ≈ 1 token
        return total

    def can_append(self, additional_tokens: int = 0) -> bool:
        """Check if appending would exceed the max prefix budget."""
        return (self.get_prefix_length() + additional_tokens) <= self.max_prefix_tokens

    def reset(self) -> None:
        """Reset the cache (start a new session)."""
        self._frozen_messages = []
        self._frozen = False