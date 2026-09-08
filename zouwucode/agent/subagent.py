"""Sub-agent system — task decomposition with isolated parallel workers.

Architecture
------------
Each SubAgent owns a **private EngineLoop** that shares the process-wide
LLM provider and tool registry but has its own PrefixCache, cache stats and
interrupt state. This isolation is what makes parallel execution safe: the
main engine's append-only prefix is never interleaved by sub-agent traffic
(the single-shared-engine design was explicitly rejected for this reason).

The SubAgentManager is the main agent's entry point:

- create / spawn sub-agents (optionally restricted to a tool whitelist)
- run them in parallel with per-agent timeouts
- cascade interrupts: when the main engine is interrupted, all running
  sub-agents are stopped through the engine's ``on_interrupt`` hook
- report status for the ``/agents``-style introspection
"""

import asyncio
import logging
import time
import uuid
from enum import Enum
from typing import Optional

from ..engine.loop import EngineLoop, TaskInterrupted, TurnContext
from ..engine.providers.base import BaseProvider, ModelResponse, ToolResult
from ..config import ZOUWUCODEConfig
from .coordinator import AgentCoordinator

logger = logging.getLogger("zouwucode.subagent")


class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SubAgent:
    """An isolated worker: private engine, optional tool whitelist."""

    def __init__(
        self,
        manager: "SubAgentManager",
        role: str,
        instructions: str,
        tool_whitelist: Optional[list[str]] = None,
        timeout: float = 600.0,
    ):
        self.id = f"sub-{uuid.uuid4().hex[:8]}"
        self.role = role
        self.instructions = instructions
        self.tool_whitelist = tool_whitelist  # None = all tools allowed
        self.timeout = timeout
        self.status = AgentStatus.PENDING
        self.result: Optional[ModelResponse] = None
        self.error: Optional[str] = None
        self.created_at = time.monotonic()
        self.duration: float = 0.0

        self._manager = manager

        # Private engine — isolated cache/stats/interrupt, shared provider.
        # Reasoning intensity is intentionally NOT re-applied here: the
        # provider instance is shared with the main engine and owns the
        # current setting.
        self.engine = EngineLoop(manager.config, manager.provider)
        self.engine.set_tool_executor(self._make_executor(manager.coordinator))

    # ── Tool execution with whitelist enforcement ────────────────────────

    def _tool_schemas(self) -> Optional[list[dict]]:
        """Tool schemas for this agent, filtered by the whitelist.

        The model can only request tools it can see in the schema list —
        passing the (filtered) schemas here is what makes the whitelist
        meaningful end-to-end.
        """
        registry = getattr(self._manager.coordinator, "tools", None)
        if registry is None:
            return None
        schemas = registry.get_schemas()
        if self.tool_whitelist is None:
            return schemas
        return [
            s for s in schemas
            if s.get("function", {}).get("name") in self.tool_whitelist
        ]

    def _make_executor(self, coordinator: AgentCoordinator):
        base = coordinator.execute_tool

        async def executor(tool_call, context: TurnContext) -> ToolResult:
            if self.tool_whitelist is not None and tool_call.name not in self.tool_whitelist:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=(
                        f"Tool '{tool_call.name}' is not allowed for sub-agent "
                        f"role '{self.role}'. Allowed: {', '.join(self.tool_whitelist)}"
                    ),
                    is_error=True,
                )
            return await base(tool_call, context)

        return executor

    # ── Execution ─────────────────────────────────────────────────────────

    async def run(self, task: str) -> ModelResponse:
        """Execute one task in this sub-agent's isolated context."""
        self.status = AgentStatus.RUNNING
        start = time.monotonic()
        logger.info("Sub-agent %s (%s) started | task=%s", self.id, self.role, task[:80])
        messages = [
            {"role": "system", "content": self.instructions},
            {"role": "user", "content": task},
        ]
        try:
            self.result = await self.engine.run(
                messages=messages,
                tools=self._tool_schemas(),
            )
            self.status = AgentStatus.COMPLETED
            logger.info(
                "Sub-agent %s completed in %.1fs | content=%s",
                self.id, time.monotonic() - start,
                (self.result.content or "")[:120],
            )
            return self.result
        except TaskInterrupted as exc:
            self.status = AgentStatus.CANCELLED
            self.error = str(exc)
            logger.warning("Sub-agent %s cancelled: %s", self.id, exc)
            raise
        except Exception as exc:
            self.status = AgentStatus.FAILED
            self.error = str(exc)
            logger.error("Sub-agent %s failed: %s", self.id, exc)
            raise
        finally:
            self.duration = time.monotonic() - start

    def interrupt(self) -> bool:
        return self.engine.request_interrupt(reason=f"sub-agent {self.id} cascade")

    def summary(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "status": self.status.value,
            "duration": round(self.duration, 1),
            "error": self.error,
            "tools": self.tool_whitelist or "all",
        }


class SubAgentManager:
    """Creates, coordinates and monitors sub-agents for the main agent."""

    def __init__(
        self,
        config: ZOUWUCODEConfig,
        provider: BaseProvider,
        coordinator: AgentCoordinator,
    ):
        self.config = config
        self.provider = provider
        self.coordinator = coordinator
        self._agents: dict[str, SubAgent] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def create(
        self,
        role: str,
        instructions: str,
        tools: Optional[list[str]] = None,
        timeout: Optional[float] = None,
    ) -> SubAgent:
        """Create a sub-agent. ``tools`` restricts it to a whitelist."""
        if len(self._agents) >= self.config.subagent.max_agents:
            raise RuntimeError(
                f"Sub-agent limit reached (max_agents={self.config.subagent.max_agents})."
            )
        agent = SubAgent(
            manager=self,
            role=role,
            instructions=instructions,
            tool_whitelist=tools,
            timeout=timeout if timeout is not None else self.config.subagent.default_timeout,
        )
        self._agents[agent.id] = agent
        return agent

    def bind_main_engine(self, engine: EngineLoop) -> None:
        """Cascade interrupts: interrupting the main engine stops all
        running sub-agents via the engine's on_interrupt hook."""
        engine.on_interrupt = self.interrupt_all

    def interrupt_all(self) -> int:
        """Interrupt every currently-running sub-agent. Returns count."""
        count = 0
        for agent in self._agents.values():
            if agent.status is AgentStatus.RUNNING:
                if agent.interrupt():
                    count += 1
        if count:
            logger.warning("Interrupted %d running sub-agent(s).", count)
        return count

    # ── Parallel orchestration ────────────────────────────────────────────

    async def run_parallel(
        self,
        specs: list[dict],
    ) -> list[SubAgent]:
        """Run sub-agents concurrently and wait for all of them.

        Args:
            specs: list of {"role", "instructions", "task"} dicts; optional
                   "tools" (whitelist) and "timeout" per spec.

        The main engine stays untouched: each worker uses its own engine.
        Failed workers do not abort siblings (exceptions are captured on
        the SubAgent record and re-raised per-agent, not to the caller).
        """
        if not specs:
            return []
        # Validate up-front so a malformed spec cannot leak half-created
        # agents into the registry.
        for i, spec in enumerate(specs):
            if not isinstance(spec, dict):
                raise ValueError(
                    f"tasks[{i}] must be an object with a 'task' field, "
                    f"got {type(spec).__name__}."
                )
            if not str(spec.get("task", "")).strip():
                raise ValueError(f"tasks[{i}] is missing a non-empty 'task' field.")

        max_agents = self.config.subagent.max_agents
        if len(specs) > max_agents:
            raise RuntimeError(
                f"Requested {len(specs)} sub-agents but max_agents={max_agents}."
            )

        agents = [
            self.create(
                role=str(spec.get("role", "worker")),
                instructions=str(spec.get("instructions", "You are a focused worker agent.")),
                tools=spec.get("tools"),
                timeout=spec.get("timeout"),
            )
            for spec in specs
        ]
        tasks = [agent.run(str(spec.get("task", ""))) for agent, spec in zip(agents, specs)]

        # Per-agent timeout, siblings unaffected by one another's outcome.
        outcomes = await asyncio.gather(
            *(asyncio.wait_for(t, timeout=a.timeout) for t, a in zip(tasks, agents)),
            return_exceptions=True,
        )
        for agent, outcome in zip(agents, outcomes):
            if isinstance(outcome, asyncio.TimeoutError):
                agent.status = AgentStatus.FAILED
                agent.error = f"timed out after {agent.timeout:.0f}s"
                agent.engine.request_interrupt(reason="sub-agent timeout")
                logger.error("Sub-agent %s timed out.", agent.id)
            elif isinstance(outcome, BaseException) and agent.status is AgentStatus.RUNNING:
                agent.status = AgentStatus.FAILED
                agent.error = str(outcome)
        return agents

    # ── Introspection ─────────────────────────────────────────────────────

    def status_summary(self) -> list[dict]:
        return [a.summary() for a in self._agents.values()]
