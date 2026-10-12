"""Prefix-cache management — the core of the cache-first strategy.

This module implements the cache-tracking and statistics that mirror
Reasonix's approach: keep the prefix byte-identical so DeepSeek's
server-side cache stays hot.
"""

import time
from collections import deque
from dataclasses import dataclass, field

from ..tokens import estimate_tokens


# Per-model USD price per 1M tokens: (input_uncached, input_cached, output).
# Cost was previously hardcoded to DeepSeek V4-Flash rates regardless of the
# model, so non-DeepSeek turns — and the engine.max_cost_usd breaker — were
# wrong. First matching substring wins; unknown models fall back to V4-Flash.
PRICING = {
    "deepseek-v4-flash": (0.14, 0.028, 0.28),
    "deepseek-v4-pro": (0.27, 0.027, 1.10),
    "deepseek-chat": (0.27, 0.07, 1.10),
    "deepseek-reasoner": (0.55, 0.14, 2.19),
    "gpt-4o-mini": (0.15, 0.075, 0.60),
    "gpt-4o": (2.50, 1.25, 10.00),
}
DEFAULT_PRICING = (0.14, 0.028, 0.28)


@dataclass
class CacheStats:
    """Real-time cache performance statistics.

    ``window_size`` (from config.cache.stats_window) sets the length of the
    rolling hit-hit/miss windows behind ``window_hit_rate``.
    """

    window_size: int = 50
    model: str = ""
    total_requests: int = 0
    cache_hits: int = 0
    total_prompt_tokens: int = 0
    cached_prompt_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    window_hits: deque = field(default=None)
    window_misses: deque = field(default=None)

    def __post_init__(self):
        size = max(1, int(self.window_size or 50))
        self.window_hits = deque(maxlen=size)
        self.window_misses = deque(maxlen=size)

    def _pricing(self):
        """(input_uncached, input_cached, output) USD per 1M tokens."""
        m = (self.model or "").lower()
        for key, price in PRICING.items():
            if key in m:
                return price
        return DEFAULT_PRICING
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
        inp, cached_price, _ = self._pricing()
        # Net saving: cached tokens billed at the cached rate, not the full rate.
        return self.cached_prompt_tokens * max(0.0, inp - cached_price) / 1_000_000

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

        # Cost from this model's own price row (falls back to V4-Flash).
        inp, cached_price, out_price = self._pricing()
        uncached_tokens = max(0, prompt_tokens - cached_tokens)  # guard bad usage
        self.total_cost += (uncached_tokens * inp
                            + cached_tokens * cached_price
                            + output_tokens * out_price) / 1_000_000


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
        # Warn (once) when the prefix exceeds its budget: can_append() used to
        # have zero production callers, so the limit was never even checked.
        self._over_budget_warned = False

    def freeze(self, messages: list[dict]) -> None:
        """Freeze the initial message prefix once at session start."""
        if not self._frozen:
            self._frozen_messages = list(messages)
            self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def append(self, message: dict) -> None:
        """Append a message — NEVER prepend or reorder.

        Signals (once per instance) when the prefix crosses max_prefix_tokens;
        the append-only contract keeps the data intact — this is a heads-up
        that cost/latency may rise, not a hard stop.
        """
        self._frozen_messages.append(message)
        if not self._over_budget_warned and not self.can_append():
            self._over_budget_warned = True
            import logging
            logging.getLogger("zouwucode.cache").warning(
                "Prefix cache over budget: ~%d tokens > max_prefix_tokens=%d "
                "(data kept; consider --compress to cap prompt size).",
                self.get_prefix_length(), self.max_prefix_tokens)

    def get_prefix(self) -> list[dict]:
        """Return the full append-only message list."""
        return list(self._frozen_messages)

    def get_prefix_length(self) -> int:
        """Rough token count of the current prefix."""
        total = 0
        for msg in self._frozen_messages:
            total += estimate_tokens(msg.get("content") or "")
        return total

    def can_append(self, additional_tokens: int = 0) -> bool:
        """Check if appending would exceed the max prefix budget."""
        return (self.get_prefix_length() + additional_tokens) <= self.max_prefix_tokens

    def reset(self) -> None:
        """Reset the cache (start a new session)."""
        self._frozen_messages = []
        self._frozen = False