"""Sisyphus orchestrator — the heart of the oh-my-opencode port.

Sisyphus classifies intent (IntentGate), then routes to the right agent:
  - planning / architecture → Prometheus / Oracle
  - implementation / fix    → Atlas (which runs Sisyphus-Junior workers)
  - research / explore      → Librarian / Explore
  - quick question          → answered directly

The orchestrator is UI-agnostic: it emits structured events that the CLI,
TUI, and Web UI render however they like.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.engine.loop import EngineLoop
from .agents import AgentDef, compose_worker_system, get_agent
from .categories import get_category, CategorySpec
from .intent_gate import IntentGate, Intent, intent_label
from .boulder import BoulderState
from .planner import Planner
from .atlas import Atlas, OrchestrationResult


# Event callback: (event_type, message, payload)
EventCallback = Callable[[str, str, dict], None]


class SisyphusOrchestrator:
    """Main orchestrator — plans, delegates, and verifies (Sisyphus)."""

    def __init__(
        self,
        config: ZOUWUCODEConfig,
        engine: EngineLoop,
        omo_dir: Optional[Path] = None,
        intent_gate: Optional[IntentGate] = None,
        on_event: Optional[EventCallback] = None,
        skills_manager=None,
    ):
        self.config = config
        self.engine = engine
        self.omo_dir = (omo_dir or Path.cwd() / ".omo").resolve()
        self.on_event = on_event or (lambda *_: None)
        self.intent_gate = intent_gate or IntentGate()
        self.skills_manager = skills_manager
        self.boulder = BoulderState(self.omo_dir)
        self.planner = Planner(engine, self.omo_dir, on_event=self.on_event)
        self.atlas = Atlas(engine, self.boulder, self.omo_dir, on_event=self.on_event)

    # ── Event helpers ────────────────────────────────────────────────────────

    def _emit(self, etype: str, message: str, payload: Optional[dict] = None) -> None:
        self.on_event(etype, message, payload or {})

    # ── Public entry points ──────────────────────────────────────────────────

    async def ultrawork(self, request: str) -> OrchestrationResult:
        """Ultrawork mode — full autonomy. The agent figures everything out.

        Equivalent to oh-my-opencode's `ultrawork` / `ulw` command.
        """
        self._emit("system", "Ultrawork started — Sisyphus takes over.")

        # 1. Classify intent
        result = await self.intent_gate.classify_async(request)
        self._emit("intent", f"Intent classified: {intent_label(result.intent)} ({result.confidence:.0%})")

        # 2. Route by intent
        if result.intent in (Intent.QUICK_QUESTION,):
            return await self._handle_quick(request)

        if result.intent in (Intent.RESEARCH, Intent.EXPLORE):
            return await self._handle_research(request, result.intent)

        if result.intent in (Intent.ARCHITECTURE, Intent.REVIEW):
            return await self._handle_consult(request)

        # 3. Implementation-family intents → Plan → Execute pipeline
        return await self._handle_implementation(request, result.intent)

    async def start_work(self, session_id: Optional[str] = None,
                         plan_name: Optional[str] = None) -> OrchestrationResult:
        """Execute the active plan (Atlas), resuming if work is in progress.

        Equivalent to oh-my-opencode's `/start-work`.
        """
        if not self.boulder.exists():
            # Find the most recent plan and start fresh
            plan = self.planner.latest_plan()
            if plan is None:
                self._emit("error", "No active plan found. Run /plan <task> first.")
                return OrchestrationResult(False, Intent.PLANNING, content="No active plan found.")
            plan_name = plan.stem
            todos = self.planner.parse_plan_todos(plan)
            self.boulder.start(plan_name, str(plan), todos, session_id)
            self._emit("system", f"Fresh start — executing plan '{plan_name}' ({len(todos)} tasks).")
        else:
            plan_name = self.boulder.plan_name or "active plan"
            self.boulder.add_session(session_id) if session_id else None
            prog = self.boulder.progress
            self._emit("system", f"Resuming '{plan_name}' — {prog['completed']}/{prog['total']} tasks complete.")

        return await self.atlas.execute(session_id=session_id)

    async def run_plan(self, task: str, interview: bool = True,
                       high_accuracy: bool = False) -> OrchestrationResult:
        """Run Prometheus planning: interview → research → write plan.

        Equivalent to oh-my-opencode's `@plan` / Prometheus mode.
        """
        plan_path = await self.planner.create_plan(
            task,
            interactive=interview,
            high_accuracy=high_accuracy,
            metis=self._metis,
            momus=self._momus,
        )
        if plan_path is None:
            return OrchestrationResult(False, Intent.PLANNING, content="Plan creation cancelled or failed.")
        todos = self.planner.parse_plan_todos(plan_path)
        self._emit("plan", f"Plan ready: {plan_path.name} ({len(todos)} tasks). Run /start-work to execute.")
        return OrchestrationResult(
            True, Intent.PLANNING,
            content=f"Plan created: {plan_path}",
            details=[plan_path.name],
            plan_name=plan_path.stem,
            tasks_total=len(todos),
        )

    # ── Intent handlers ──────────────────────────────────────────────────────

    async def _handle_quick(self, request: str) -> OrchestrationResult:
        """Quick question — answered directly with a quick-category worker."""
        self._emit("system", "Quick question — answering directly.")
        content = await self._run_subagent("quick", "sisyphus-junior", request)
        return OrchestrationResult(True, Intent.QUICK_QUESTION, content=content)

    async def _handle_research(self, request: str, intent: Intent) -> OrchestrationResult:
        """Research/explore — delegate to Librarian or Explore worker."""
        self._emit("system", f"Delegating to research agents ({intent_label(intent)}).")
        agent_name = "explore" if intent == Intent.EXPLORE else "librarian"
        results = await self._run_subagents(
            [(agent_name, request)],
            category="quick",
        )
        content = "\n\n".join(r or "(no findings)" for r in results)
        return OrchestrationResult(True, intent, content=content)

    async def _handle_consult(self, request: str) -> OrchestrationResult:
        """Architecture / review — Oracle consultation."""
        self._emit("system", "Delegating to Oracle (architecture consultation).")
        content = await self._run_subagent("ultrabrain", "oracle", request)
        return OrchestrationResult(True, Intent.ARCHITECTURE, content=content)

    async def _handle_implementation(self, request: str, intent: Intent) -> OrchestrationResult:
        """Implementation-family — plan (if complex) then execute via Atlas."""
        self._emit("system", "Routing to implementation pipeline (plan → execute).")

        # Try to reuse an existing plan; otherwise create one on the fly
        plan_path = self.planner.latest_plan()
        if plan_path is None:
            plan_path = await self.planner.create_plan(
                request,
                interactive=False,  # ultrawork plans autonomously
                metis=self._metis,
                momus=None,  # skip review in ultrawork fast path
            )
        if plan_path is None:
            # Plan failed — fall back to a single deep worker
            self._emit("warning", "Plan creation failed — falling back to single worker.")
            content = await self._run_subagent("deep", "hephaestus", request)
            return OrchestrationResult(True, intent, content=content)

        # Start boulder + execute
        todos = self.planner.parse_plan_todos(plan_path)
        self.boulder.start(plan_path.stem, str(plan_path), todos)
        self._emit("system", f"Executing plan '{plan_path.stem}' via Atlas.")
        atlas_result = await self.atlas.execute()

        return OrchestrationResult(
            atlas_result.success,
            intent,
            content=atlas_result.content,
            details=atlas_result.details,
            plan_name=plan_path.stem,
            tasks_completed=atlas_result.tasks_completed,
            tasks_total=atlas_result.tasks_total,
        )

    # ── Sub-agent runners ────────────────────────────────────────────────────

    async def _run_subagent(self, category: str, agent_name: str,
                            task: str) -> str:
        """Run a single sub-agent with the given category routing."""
        spec = get_category(category)
        agent = get_agent(agent_name)
        messages = self._build_worker_messages(agent, spec, task)
        response = await self.engine.run(messages)
        return response.content or ""

    async def _run_subagents(
        self, tasks: list[tuple[str, str]], category: str = "deep"
    ) -> list[str]:
        """Run worker agents sequentially.

        The EngineLoop owns a single append-only PrefixCache plus shared
        interrupt state, so concurrent engine.run() calls would interleave
        messages and corrupt the prefix — workers must run one at a time.
        """
        spec = get_category(category)
        results: list[str] = []
        for agent_name, task in tasks:
            agent = get_agent(agent_name)
            messages = self._build_worker_messages(agent, spec, task)
            try:
                response = await self.engine.run(messages)
                results.append(response.content or "")
            except Exception as e:
                results.append(f"(error: {e})")
        return results

    def _build_worker_messages(self, agent: AgentDef, spec: CategorySpec,
                               task: str) -> list[dict]:
        """Build the message list for a worker with skill context."""
        system = compose_worker_system(agent, spec, self._skills_block())
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ]

    def _skills_block(self) -> str:
        """Project rules / skills context block, if a manager is wired."""
        if self.skills_manager is None:
            return ""
        return self.skills_manager.get_context_block()

    # ── Review sub-agents (Metis / Momus) ────────────────────────────────────

    async def _metis(self, task: str, plan_draft: str) -> str:
        """Metis gap analysis — returns feedback."""
        prompt = (
            f"User request: {task}\n\nPlan draft to review:\n{plan_draft}\n\n"
            "List gaps, hidden intentions, ambiguities, scope creep, "
            "missing acceptance criteria, and edge cases."
        )
        return await self._run_subagent("ultrabrain", "metis", prompt)

    async def _momus(self, task: str, plan_text: str) -> str:
        """Momus plan review — returns OKAY/REJECT with cited issues."""
        prompt = (
            f"User request: {task}\n\nPlan to review:\n{plan_text}\n\n"
            "Output: 'OKAY — reason' or 'REJECT — cited issues'."
        )
        return await self._run_subagent("ultrabrain", "momus", prompt)


def detect_ultrawork(message: str) -> Optional[str]:
    """Detect the ultrawork keyword and return the task text.

    Matches "ultrawork ...", "ulw ...", "ultrawork" alone (falls back to
    treating the whole message as the task).
    """
    m = re.match(r"^(?:ultrawork|ulw)\b[:\s]*(.*)$", message.strip(), re.IGNORECASE)
    if not m:
        return None
    return m.group(1).strip()
