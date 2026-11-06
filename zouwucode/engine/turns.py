"""Turn-level types shared by the engine loop and its callers.

These live in their own module so the core loop file stays focused on the
execution flow. ``zouwucode.engine.loop`` re-exports every name here, so
existing imports (``from zouwucode.engine.loop import TurnContext``) keep
working unchanged.
"""

import time
from typing import Awaitable, Callable, Optional

from .providers.base import ModelResponse, ToolCall, ToolResult


class TurnLimitExceeded(RuntimeError):
    """Raised when the engine loop hits a safety limit (rounds / timeout /
    consecutive tool errors) — prevents infinite tool-call loops."""


class TaskInterrupted(RuntimeError):
    """Raised when the user interrupts the running task.

    Distinct from TurnLimitExceeded so callers can tell "user cancelled"
    apart from "safety limit tripped" and offer recovery options."""


class TurnContext:
    """Context for a single turn in the engine loop."""

    def __init__(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        mode: str = "agent",
    ):
        self.messages = messages
        self.tools = tools or []
        self.mode = mode  # plan | agent | yolo
        self.response: Optional[ModelResponse] = None
        self.tool_results: list[ToolResult] = []
        self.start_time = time.time()
        self.duration: float = 0.0


# Type alias for the tool execution callback
ToolExecutor = Callable[[ToolCall, TurnContext], Awaitable[ToolResult]]

# Injected once when the stuck detector sees the model repeat an identical
# action+result inside the sliding window. Phrased as information, not a
# prohibition — the model decides how to change strategy.
_STUCK_NUDGE = (
    "⚠️ Stuck check: you have repeated the identical action with the "
    "identical result several times in a row. Repeating it again will not "
    "change anything. Either (a) change your approach — different tool, "
    "different arguments, or inspect a different file; (b) re-read the "
    "original task and re-plan the next concrete step; or (c) stop and "
    "summarize what you have achieved so far and what blocks the rest."
)

# Injected when the tool-round budget is exhausted but the model is still
# making progress (tools succeeding, no stuck verdict). Instead of killing a
# legitimate multi-step task mid-flight, we hand it a fresh budget with an
# instruction to either converge now or work efficiently — a genuine runaway
# still trips the hard cap once extensions run out (plus stuck / consecutive-
# error / total-timeout / cost breakers, which are unaffected).
_ROUND_EXTENSION_NUDGE = (
    "ℹ️ You have used up the planned tool-round budget for this task. You are "
    "being given a little more room. If the task is nearly done, wrap up now: "
    "stop calling tools and give your final answer. If real work remains, "
    "keep going but be efficient — batch independent steps, avoid redundant "
    "reads, and don't repeat actions that already succeeded."
)
