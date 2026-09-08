"""Cache-first engine loop — the core execution loop of ZOUWUCODE.

This is the heart of the agent, implementing the append-only, cache-first
design inspired by Reasonix. The loop:

1. Maintains a byte-stable conversation prefix
2. Sends requests to the LLM with deterministic tool schemas
3. Processes tool calls in a sandboxed environment
4. Records cache statistics for cost transparency
5. Supports Plan (read-only), Agent (interactive), and YOLO (auto) modes
"""

import asyncio
import logging
import threading
import time
from typing import Optional, Callable, Awaitable

import httpx

from .cache import PrefixCache, CacheStats
from .providers.base import (
    BaseProvider,
    ModelResponse,
    ProviderAPIError,
    ToolCall,
    ToolResult,
    Message,
)
from ..config import ZOUWUCODEConfig

logger = logging.getLogger("zouwucode.engine")


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


class EngineLoop:
    """The main append-only, cache-first agent loop.

    Usage:
        loop = EngineLoop(config, provider)
        loop.set_tool_executor(my_executor)
        response = await loop.run(messages, tools)
    """

    def __init__(
        self,
        config: ZOUWUCODEConfig,
        provider: BaseProvider,
    ):
        self.config = config
        self.provider = provider
        self.cache = PrefixCache(
            max_prefix_tokens=config.cache.max_prefix_tokens
        )
        self.stats = CacheStats()
        self._tool_executor: Optional[ToolExecutor] = None
        self._mode = config.sandbox.default_mode
        self._running = False
        # Optional UI hooks: called with the ToolCall right before it executes.
        # Lets TUI/Web render opencode-style "→ Read file …" lines.
        self.on_tool_event: Optional[Callable[[ToolCall], None]] = None
        # Optional streaming hooks: called with each incremental text delta as
        # the model generates. Lets CLI/TUI/Web render the thinking process in
        # real time (opencode-style streaming). Set to None to disable.
        self.on_thinking_delta: Optional[Callable[[str], None]] = None
        self.on_content_delta: Optional[Callable[[str], None]] = None
        # ── Interrupt support ────────────────────────────────────────────
        # threading.Event so request_interrupt() works from any thread/UI.
        self._interrupt_event = threading.Event()
        self._interrupt_reason: str = ""
        # Optional cascade hook: invoked when this engine is interrupted —
        # used by SubAgentManager to stop all running sub-agents too.
        self.on_interrupt: Optional[Callable[[], None]] = None

    # ── Interrupt API ────────────────────────────────────────────────────────

    def request_interrupt(self, reason: str = "") -> bool:
        """Request interruption of the currently running task.

        Safe to call from any thread or async context. Returns False if no
        task is running (nothing to interrupt). The interruption takes
        effect at the next safe point: LLM stream chunk, round boundary,
        or before a tool executes.
        """
        if not self._running:
            logger.info("Interrupt ignored — no task is currently running.")
            return False
        self._interrupt_reason = reason or "user requested"
        self._interrupt_event.set()
        logger.warning("Interrupt requested | reason=%s", self._interrupt_reason)
        # Cascade: let interested parties (e.g. sub-agent manager) stop too.
        if self.on_interrupt is not None:
            try:
                self.on_interrupt()
            except Exception as exc:  # never let the cascade break the stop
                logger.error("Interrupt cascade hook failed: %s", exc)
        return True

    @property
    def is_running(self) -> bool:
        """True while a task is being executed by run()."""
        return self._running

    def _check_interrupt(self, where: str) -> None:
        """Raise TaskInterrupted if an interrupt was requested."""
        if self._interrupt_event.is_set():
            raise TaskInterrupted(
                f"Task interrupted by user ({self._interrupt_reason}) — {where}"
            )

    async def _interrupt_poller(self) -> None:
        """Poll the interrupt event inside the event loop.

        A loop-internal task (not a thread-pool waiter) so cancellation is
        always clean — a blocked run_in_executor thread would leak and stall
        loop shutdown forever.
        """
        while not self._interrupt_event.is_set():
            await asyncio.sleep(0.1)

    async def _run_interruptible(self, coro, timeout: float):
        """Await *coro* but wake promptly when an interrupt is requested.

        Races the coroutine against a poller of the interrupt event (~0.1s
        latency). Also enforces *timeout*. Raises TaskInterrupted or
        asyncio.TimeoutError; the losing task is cancelled in both cases.
        """
        stream_task = asyncio.ensure_future(coro)
        poll_task = asyncio.ensure_future(self._interrupt_poller())
        # Track the stream task so an abrupt unwind (KeyboardInterrupt /
        # CancelledError arriving at the outer await) can be cleaned up.
        self._current_stream_task = stream_task
        try:
            done, pending = await asyncio.wait(
                {stream_task, poll_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if self._current_stream_task is stream_task:
                self._current_stream_task = None
        # Whatever lost the race gets cancelled.
        for task in pending:
            task.cancel()
        if stream_task not in done:
            # Timed out or interrupted before the stream finished.
            if self._interrupt_event.is_set():
                raise TaskInterrupted(
                    f"Task interrupted by user ({self._interrupt_reason}) "
                    f"— during LLM request"
                )
            raise asyncio.TimeoutError()
        return stream_task.result()

    def cancel_pending_io(self) -> None:
        """Cancel the in-flight LLM stream task, if any.

        Used when run() is unwound abruptly (KeyboardInterrupt /
        CancelledError from the UI layer) so no orphaned HTTP stream keeps
        running in the background.
        """
        task = getattr(self, "_current_stream_task", None)
        if task is not None and not task.done():
            task.cancel()
            logger.warning("Cancelled pending LLM stream task.")

    def _cleanup_interrupted_prefix(
        self,
        assistant_appended: bool,
        response: Optional[ModelResponse],
        context: "TurnContext",
    ) -> None:
        """Keep the conversation prefix API-valid after an interruption.

        If the assistant message (with tool_calls) was already appended,
        every tool call must be answered by a tool result — fill in
        synthetic "[interrupted]" results for the calls that never ran.
        """
        if assistant_appended and response is not None and response.tool_calls:
            executed_ids = {r.tool_call_id for r in context.tool_results}
            pending = [tc for tc in response.tool_calls if tc.id not in executed_ids]
            for tc in pending:
                self.cache.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": "[interrupted by user — tool not executed]",
                })
            if pending:
                logger.warning(
                    "Interrupt cleanup — %d pending tool call(s) answered "
                    "with synthetic interrupted results.",
                    len(pending),
                )

    def set_tool_executor(self, executor: ToolExecutor) -> None:
        """Set the callback for executing tool calls."""
        self._tool_executor = executor

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Switch between plan, agent, and yolo modes."""
        if mode in ("plan", "agent", "yolo"):
            self._mode = mode

    def set_reasoning_intensity(self, level: str) -> None:
        """Set reasoning effort level (low / medium / max).

        Delegates to the provider if it supports it.
        """
        if hasattr(self.provider, "set_reasoning_effort"):
            self.provider.set_reasoning_effort(level)

    def freeze_session(self, system_prompt: str, tool_schemas: list[dict]) -> None:
        """Freeze the system prompt and tool schemas for cache stability.

        This MUST be called once at session start before any turns.
        """
        # Freeze in the provider (DeepSeek-specific)
        if hasattr(self.provider, "freeze_system_prompt"):
            self.provider.freeze_system_prompt(system_prompt)
        if hasattr(self.provider, "freeze_tool_schemas"):
            self.provider.freeze_tool_schemas(tool_schemas)

        # Freeze in the local cache tracker
        self.cache.reset()
        frozen = [{"role": "system", "content": system_prompt}]
        self.cache.freeze(frozen)

    async def _stream_once(
        self,
        messages: list[dict],
        tools: Optional[list[dict]],
        temperature: float,
        max_tokens: int,
    ) -> ModelResponse:
        """Stream one LLM request and aggregate deltas into a ModelResponse.

        Wrapped in asyncio.wait_for so a hung connection cannot stall the
        task forever (timeout: config.engine.turn_timeout_seconds).
        """
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls: Optional[list[ToolCall]] = None
        usage: dict = {}
        cache_hit = False

        engine_cfg = self.config.engine
        attempt = 0
        while True:
            streamed_any = False
            try:
                async for chunk in self.provider.chat_stream(
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    # Safe point: user interrupt mid-stream (checked per chunk
                    # so a long generation stops promptly).
                    self._check_interrupt("streaming LLM response")
                    if chunk.content:
                        streamed_any = True
                        content_parts.append(chunk.content)
                        if self.on_content_delta is not None:
                            try:
                                self.on_content_delta(chunk.content)
                            except Exception:
                                pass
                    if chunk.thinking:
                        streamed_any = True
                        thinking_parts.append(chunk.thinking)
                        if self.on_thinking_delta is not None:
                            try:
                                self.on_thinking_delta(chunk.thinking)
                            except Exception:
                                pass
                    if chunk.tool_calls:
                        streamed_any = True
                        tool_calls = chunk.tool_calls
                    if chunk.usage:
                        usage = chunk.usage
                    if chunk.cache_hit:
                        cache_hit = chunk.cache_hit
                break  # stream completed successfully
            except TaskInterrupted:
                raise
            except (ProviderAPIError, httpx.TransportError) as exc:
                # Retry only transient failures (429 / 5xx / transport) and
                # only while nothing has streamed to the UI yet — replaying
                # mid-stream would duplicate rendered deltas.
                transient = (
                    isinstance(exc, httpx.TransportError)
                    or (isinstance(exc, ProviderAPIError)
                        and (exc.status_code == 0 or exc.status_code == 429
                             or exc.status_code >= 500))
                )
                if (streamed_any or not transient
                        or attempt >= engine_cfg.max_llm_retries):
                    raise
                attempt += 1
                delay = engine_cfg.retry_base_delay_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "LLM request failed (%s) — transient, retry %d/%d in %.1fs",
                    exc, attempt, engine_cfg.max_llm_retries, delay,
                )
                await asyncio.sleep(delay)

        if not content_parts and not thinking_parts and not tool_calls:
            raise RuntimeError("No response from LLM provider")

        return ModelResponse(
            content="".join(content_parts),
            thinking="".join(thinking_parts),
            tool_calls=tool_calls or [],
            usage=usage,
            cache_hit=cache_hit,
        )

    async def run(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.0,
        max_tokens: int = 65536,
    ) -> ModelResponse:
        """Execute a single task in the engine loop (iterative, bounded).

        The loop iterates — each round runs one LLM request plus its tool
        calls — instead of recursing. Three safety limits guard against
        infinite loops and unrecoverable stalls:

        1. max_tool_rounds — hard cap on tool-call rounds
        2. task_timeout_seconds — wall-clock budget for the whole task
        3. max_consecutive_tool_errors — stops the model from repeatedly
           hitting the same failing tool

        Exceeding any limit raises TurnLimitExceeded (callers like Atlas
        already handle it by marking the task failed and moving on).
        """
        engine_cfg = self.config.engine
        self._running = True
        context = TurnContext(messages, tools, self._mode)
        task_start = time.monotonic()
        deadline = task_start + engine_cfg.task_timeout_seconds
        consecutive_tool_errors = 0
        response: Optional[ModelResponse] = None
        assistant_appended = False

        logger.info(
            "Task start | mode=%s | rounds_cap=%d | task_timeout=%.0fs | "
            "user_messages=%d",
            self._mode, engine_cfg.max_tool_rounds,
            engine_cfg.task_timeout_seconds, len(messages),
        )

        try:
            # Append user messages to the prefix
            for msg in messages:
                self.cache.append(msg)

            for round_no in range(1, engine_cfg.max_tool_rounds + 1):
                # ── Safety check: overall task deadline ──
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.error(
                        "Task exceeded total timeout (%.0fs) after %d round(s) "
                        "— aborting to avoid an unrecoverable stall.",
                        engine_cfg.task_timeout_seconds, round_no - 1,
                    )
                    raise TurnLimitExceeded(
                        f"Task timed out after {engine_cfg.task_timeout_seconds:.0f}s "
                        f"({round_no - 1} tool rounds)."
                    )

                # ── Safe point: user interrupt at round boundary ──
                self._check_interrupt(f"round {round_no} boundary")

                # ── Round start: one LLM request (bounded + interruptible) ──
                round_start = time.monotonic()
                turn_timeout = min(engine_cfg.turn_timeout_seconds, remaining)
                logger.info("Round %d/%d — requesting LLM…",
                            round_no, engine_cfg.max_tool_rounds)
                try:
                    response = await self._run_interruptible(
                        self._stream_once(
                            messages=self.cache.get_prefix(),
                            tools=tools,
                            temperature=temperature,
                            max_tokens=max_tokens,
                        ),
                        timeout=turn_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        "Round %d — LLM request timed out after %.0fs "
                        "— aborting task.",
                        round_no, turn_timeout,
                    )
                    raise TurnLimitExceeded(
                        f"LLM request timed out after {turn_timeout:.0f}s "
                        f"in round {round_no}."
                    )
                logger.info(
                    "Round %d — LLM responded in %.1fs | tool_calls=%d | "
                    "cache_hit=%s",
                    round_no, time.monotonic() - round_start,
                    len(response.tool_calls), response.cache_hit,
                )

                context.response = response

                # Record cache statistics
                self.stats.record_turn(response.cache_hit, response.usage)

                # Append assistant response to cache so tool results are valid
                assistant_msg = {"role": "assistant", "content": response.content or ""}
                if response.tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": tc.arguments},
                        }
                        for tc in response.tool_calls
                    ]
                self.cache.append(assistant_msg)
                assistant_appended = True

                # ── Tool execution ──
                if not response.tool_calls or self._mode == "plan":
                    # No tool calls (or read-only plan mode) — task finished.
                    if self._mode == "plan" and response.tool_calls:
                        logger.info(
                            "Round %d — plan mode: skipping %d tool call(s).",
                            round_no, len(response.tool_calls),
                        )
                    break

                if not self._tool_executor:
                    # Nothing can execute the tools — stop here instead of
                    # feeding the model an endless loop of unanswered calls.
                    logger.warning(
                        "Round %d — no tool executor set; returning response "
                        "with %d unexecuted tool call(s).",
                        round_no, len(response.tool_calls),
                    )
                    break

                for tool_call in response.tool_calls:
                    # Safe point: user interrupt before each tool executes.
                    self._check_interrupt(f"before tool '{tool_call.name}'")

                    # Notify UI (opencode-style "→ Read file …" rendering)
                    if self.on_tool_event is not None:
                        try:
                            self.on_tool_event(tool_call)
                        except Exception:
                            pass

                    exec_start = time.monotonic()
                    result = await self._tool_executor(tool_call, context)
                    context.tool_results.append(result)
                    self.cache.append(result.to_dict())
                    logger.info(
                        "Round %d — tool '%s' finished in %.2fs | error=%s | "
                        "output=%s",
                        round_no, tool_call.name,
                        time.monotonic() - exec_start, result.is_error,
                        (result.content[:120] + "…") if result.content and len(result.content) > 120 else result.content,
                    )

                    # Track consecutive failures — the classic infinite-loop
                    # trigger (model keeps retrying a tool that always fails).
                    if result.is_error:
                        consecutive_tool_errors += 1
                    else:
                        consecutive_tool_errors = 0

                if consecutive_tool_errors >= engine_cfg.max_consecutive_tool_errors:
                    logger.error(
                        "Round %d — %d consecutive tool errors (threshold=%d) "
                        "— aborting task to break the retry loop.",
                        round_no, consecutive_tool_errors,
                        engine_cfg.max_consecutive_tool_errors,
                    )
                    raise TurnLimitExceeded(
                        f"Aborted after {consecutive_tool_errors} consecutive "
                        f"tool errors in round {round_no}."
                    )

                # Tool results were appended to the cache — loop for the
                # next round (the model sees them via cache.get_prefix()).
            else:
                # for-loop exhausted without break → round cap reached
                logger.error(
                    "Task hit max_tool_rounds=%d — aborting. This usually "
                    "means the model kept issuing tool calls without "
                    "converging (check tool error logs above).",
                    engine_cfg.max_tool_rounds,
                )
                raise TurnLimitExceeded(
                    f"Exceeded max_tool_rounds={engine_cfg.max_tool_rounds}."
                )

        except TaskInterrupted:
            self._cleanup_interrupted_prefix(assistant_appended, response, context)
            logger.warning(
                "Task interrupted by user after %.1fs | tool_results=%d",
                time.monotonic() - task_start, len(context.tool_results),
            )
            raise

        except (KeyboardInterrupt, asyncio.CancelledError):
            # Abrupt unwind from the UI layer (e.g. CLI Ctrl+C aborting the
            # await). Stop any orphaned stream and keep the prefix valid.
            self.cancel_pending_io()
            self._cleanup_interrupted_prefix(assistant_appended, response, context)
            logger.warning(
                "Task aborted abruptly (KeyboardInterrupt/CancelledError) "
                "after %.1fs | tool_results=%d",
                time.monotonic() - task_start, len(context.tool_results),
            )
            raise

        finally:
            context.duration = time.time() - context.start_time
            self._running = False
            # Reset the interrupt signal so the next task starts clean —
            # critical for consecutive interrupt cycles.
            self._interrupt_event.clear()
            self._interrupt_reason = ""
            logger.info(
                "Task end | duration=%.1fs | rounds_used<=%d | "
                "tool_results=%d",
                time.monotonic() - task_start,
                engine_cfg.max_tool_rounds, len(context.tool_results),
            )

        assert response is not None
        return response

    async def plan(self, messages: list[dict]) -> ModelResponse:
        """Execute a plan-only turn (read-only, no tool execution)."""
        old_mode = self._mode
        self._mode = "plan"
        try:
            return await self.run(messages)
        finally:
            self._mode = old_mode

    def get_cache_summary(self) -> dict:
        """Return a human-readable cache performance summary."""
        return {
            "hit_rate": f"{self.stats.hit_rate * 100:.1f}%",
            "window_hit_rate": f"{self.stats.window_hit_rate * 100:.1f}%",
            "total_cost": f"${self.stats.total_cost:.4f}",
            "estimated_savings": f"${self.stats.estimated_savings:.4f}",
            "total_requests": self.stats.total_requests,
            "cached_token_ratio": f"{self.stats.cached_token_ratio * 100:.1f}%",
            "uptime": f"{self.stats.uptime / 60:.1f}m",
        }