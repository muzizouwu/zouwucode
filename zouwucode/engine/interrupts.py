"""Interrupt handling for the engine loop.

Extracted from ``EngineLoop`` so the core loop file stays readable. The
method names are unchanged and ``EngineLoop`` inherits them, so every
caller (CLI / TUI / WebUI / sub-agent manager) behaves exactly as before.
"""

import asyncio
import logging
from typing import Optional

from .providers.base import ModelResponse
from .turns import TaskInterrupted, TurnContext

logger = logging.getLogger("zouwucode.engine")


class InterruptMixin:
    """Interrupt API + safe-point helpers mixed into ``EngineLoop``.

    Relies on state created by ``EngineLoop.__init__``:
    ``_running``, ``_interrupt_event``, ``_interrupt_reason``, ``on_interrupt``
    and ``cache``.
    """

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
