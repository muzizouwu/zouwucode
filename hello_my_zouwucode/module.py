"""hello-my-zouwucode — ZOUWUCODE 可扩展模块（oh-my-opencode 复刻）.

Exposes the multi-agent orchestration system as a loadable ZOUWUCODE module:

- ``handle_message``  — "ultrawork …" / "ulw …" message prefix triggers the
  full-autonomy pipeline (intent → route → plan → execute).
- ``handle_command``  — /hello-plan, /hello-start-work, /hello-status,
  /hello-agents, /hello-categories, /hello-ultrawork.
- ``on_event``        — forwards orchestrator progress to the host UI.

The module keeps all core functionality (11 agents, IntentGate, Boulder,
Notepad, Prometheus planning, Atlas execution) and is loaded on demand by
ZOUWUCODE via ``config.hello_my_zouwucode.enabled``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from zouwucode.config import ZOUWUCODEConfig

from .orchestrator import SisyphusOrchestrator, detect_ultrawork


class HelloMyZouwucodeModule:
    """The hello-my-zouwucode extension module for ZOUWUCODE."""

    name = "hello-my-zouwucode"
    version = "1.0.0"
    description = (
        "Multi-agent orchestration (ported from oh-my-opencode): "
        "11 specialised agents, IntentGate routing, Boulder state, "
        "Notepad wisdom, ultrawork full-autonomy pipeline."
    )
    commands = [
        "/hello-ultrawork <task>",
        "/hello-plan <task>",
        "/hello-start-work",
        "/hello-status",
        "/hello-agents",
        "/hello-categories",
    ]

    def __init__(
        self,
        engine,
        config: Optional[ZOUWUCODEConfig] = None,
        on_event: Optional[Callable[[str, str, dict], None]] = None,
        skills_manager=None,
        state_dir: Optional[Path] = None,
    ):
        self.config = config or ZOUWUCODEConfig()
        self.engine = engine
        self._on_event = on_event
        self.skills_manager = skills_manager
        self.state_dir = state_dir or Path(self.config.hello_my_zouwucode.state_dir).resolve()

        from .intent_gate import IntentGate

        self.orchestrator = SisyphusOrchestrator(
            self.config,
            self.engine,
            omo_dir=self.state_dir,
            intent_gate=IntentGate(),
            on_event=self._forward_event,
            skills_manager=skills_manager,
        )

    # ── Event forwarding ─────────────────────────────────────────────────

    def _forward_event(self, etype: str, message: str, payload: dict) -> None:
        if self._on_event is not None:
            self._on_event(etype, message, payload)

    def on_event(self, etype: str, message: str, payload: dict) -> None:
        """Host may push module events here too (already forwarded by orchestrator)."""
        self._forward_event(etype, message, payload)

    # ── Message interception ─────────────────────────────────────────────

    async def handle_message(self, text: str) -> Optional[dict]:
        """Handle "ultrawork …" / "ulw …" message prefixes."""
        task = detect_ultrawork(text)
        if task is None:
            return None
        return await self._run_ultrawork(task)

    # ── Command handling ─────────────────────────────────────────────────

    async def handle_command(
        self, command: str, arg: str = "", context: Optional[dict] = None
    ) -> Optional[dict]:
        if command == "/hello-ultrawork":
            if not arg:
                return {
                    "title": "Usage",
                    "content": "Usage: /hello-ultrawork <task>  (or prefix any message with 'ultrawork')",
                }
            return await self._run_ultrawork(arg)

        if command == "/hello-plan":
            if not arg:
                return {
                    "title": "Usage",
                    "content": "Usage: /hello-plan <task>  — Prometheus planning",
                }
            return await self._run_plan(arg)

        if command == "/hello-start-work":
            session_id = (context or {}).get("session_id")
            return await self._run_start_work(session_id)

        if command == "/hello-status":
            return self._status()

        if command == "/hello-agents":
            return self._agents()

        if command == "/hello-categories":
            return self._categories()

        return None

    # ── Orchestration runners ────────────────────────────────────────────

    async def _run_ultrawork(self, task: str) -> dict:
        result = await self.orchestrator.ultrawork(task)
        return {
            "title": "Ultrawork",
            "content": result.content or "",
            "details": result.details,
            "intent": getattr(result, "intent", None).value
            if getattr(result, "intent", None) is not None
            else None,
            "plan_name": result.plan_name,
            "tasks_completed": result.tasks_completed,
            "tasks_total": result.tasks_total,
        }

    async def _run_plan(self, task: str) -> dict:
        result = await self.orchestrator.run_plan(
            task,
            interview=self.config.hello_my_zouwucode.interactive_planning,
            high_accuracy=False,
        )
        return {
            "title": "Prometheus Plan",
            "content": result.content or "",
            "details": result.details,
            "plan_name": result.plan_name,
        }

    async def _run_start_work(self, session_id: Optional[str] = None) -> dict:
        result = await self.orchestrator.start_work(session_id=session_id)
        return {
            "title": "Atlas Start Work",
            "content": result.content or "",
            "details": result.details,
            "tasks_completed": result.tasks_completed,
            "tasks_total": result.tasks_total,
        }

    # ── Status / inventory ───────────────────────────────────────────────

    def _status(self) -> dict:
        lines = []
        lines.append(self.orchestrator.boulder.summary())
        plan_name = self.orchestrator.boulder.plan_name
        if plan_name:
            from .notepad import Notepad

            lines.append(Notepad(plan_name, self.orchestrator.omo_dir).summary())
        from .agents import list_agents
        from .categories import list_categories

        lines.append(
            f"Agents: {len(list_agents())} | Categories: {len(list_categories())} | "
            f"Module: {self.name} v{self.version}"
        )
        return {"title": "hello-my-zouwucode status", "content": "\n".join(lines)}

    def _agents(self) -> dict:
        from .agents import AGENTS

        lines = [f"{name:<20} [{agent.mode.value}] {agent.purpose}" for name, agent in AGENTS.items()]
        return {"title": "Agent Inventory (hello-my-zouwucode)", "content": "\n".join(lines)}

    def _categories(self) -> dict:
        from .categories import CATEGORY_DEFAULTS

        lines = [
            f"{name:<20} reasoning={spec.reasoning_intensity:<6} {spec.use_for}"
            for name, spec in CATEGORY_DEFAULTS.items()
        ]
        return {"title": "Task Categories", "content": "\n".join(lines)}

    # ── Web/status payload ───────────────────────────────────────────────

    def status_payload(self) -> dict:
        """Structured status for the Web UI /api/modules endpoint."""
        from .agents import list_agents
        from .categories import list_categories

        return {
            "module": self.name,
            "version": self.version,
            "agents": len(list_agents()),
            "categories": len(list_categories()),
            "boulder": self.orchestrator.boulder.summary(),
            "plan": self.orchestrator.boulder.plan_name,
        }
