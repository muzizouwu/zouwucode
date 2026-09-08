"""Atlas — plan executor (ported from oh-my-opencode).

Atlas is the orchestra conductor: it reads the plan, accumulates wisdom in
the plan's notepad, delegates each task to a Sisyphus-Junior worker,
verifies every result, and updates the boulder. Atlas orchestrates;
workers write the code.

    .omo/
    ├── boulder.json          # Active plan + task status (cross-session)
    └── notepads/{plan}/      # Wisdom accumulation (learnings/issues/...)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from zouwucode.engine.loop import EngineLoop
from .agents import compose_worker_system, get_agent
from .boulder import BoulderState
from .categories import get_category
from .notepad import Notepad

# Event callback: (event_type, message, payload)
EventCallback = Callable[[str, str, dict], None]


@dataclass
class OrchestrationResult:
    """Final result of an orchestrated run (shared with the orchestrator)."""

    success: bool
    intent: object = None      # Intent enum member or a plain label string
    content: str = ""
    details: list[str] = field(default_factory=list)
    plan_name: Optional[str] = None
    tasks_completed: int = 0
    tasks_total: int = 0


class Atlas:
    """Executes the active boulder plan by delegating to workers."""

    def __init__(
        self,
        engine: EngineLoop,
        boulder: BoulderState,
        omo_dir: Path,
        on_event: Optional[EventCallback] = None,
    ):
        self.engine = engine
        self.boulder = boulder
        self.omo_dir = Path(omo_dir).resolve()
        self.on_event = on_event or (lambda *_: None)

    def _emit(self, etype: str, message: str, payload: Optional[dict] = None) -> None:
        self.on_event(etype, message, payload or {})

    # ── Execution ────────────────────────────────────────────────────────────

    async def execute(self, session_id: Optional[str] = None) -> OrchestrationResult:
        """Execute the active plan task-by-task.

        Each pending todo is delegated to a Sisyphus-Junior worker with the
        plan context and accumulated notepad wisdom. Results are verified and
        recorded back to the boulder.
        """
        plan_name = self.boulder.plan_name
        if not plan_name:
            return OrchestrationResult(
                False, "plan-execution",
                content="No active plan in boulder. Run /plan first.",
            )

        notepad = Notepad(plan_name, self.omo_dir)
        todos = self.boulder.remaining()
        total = self.boulder.progress["total"]
        completed_before = self.boulder.progress["completed"]
        self._emit("system", f"Atlas executing '{plan_name}' — {len(todos)} remaining task(s).")

        details: list[str] = []
        for todo in todos:
            task_id = todo["id"]
            title = todo.get("title", "(untitled)")
            category = todo.get("category", "deep") or "deep"

            self.boulder.set_todo_in_progress(task_id)
            self._emit("task", f"▶ {title}", {"task_id": task_id, "status": "in_progress"})

            try:
                result_text = await self._run_worker(todo, plan_name, notepad, category)
            except Exception as exc:  # noqa: BLE001 — worker crash must not kill Atlas
                self.boulder.fail_todo(task_id)
                notepad.add_issue(f"{title} — worker error: {exc}")
                self._emit("task", f"✗ {title} — error: {exc}", {"task_id": task_id, "status": "failed"})
                details.append(f"✗ {title} — error")
                continue

            if result_text and result_text.strip():
                self.boulder.complete_todo(task_id)
                notepad.add_verification(f"{title} — completed with response")
                # Harvest a learning from the worker's final summary.
                learning = self._extract_learning(result_text)
                if learning:
                    notepad.add_learning(f"{title}: {learning}")
                self._emit("task", f"✓ {title}", {"task_id": task_id, "status": "done"})
                details.append(f"✓ {title}")
            else:
                self.boulder.fail_todo(task_id)
                notepad.add_issue(f"{title} — worker returned empty result")
                self._emit("task", f"✗ {title} — empty result", {"task_id": task_id, "status": "failed"})
                details.append(f"✗ {title} — empty result")

        done = self.boulder.progress["completed"]
        self._emit("system", f"Atlas finished — {done}/{total} tasks complete.")
        return OrchestrationResult(
            success=done > completed_before,
            intent="plan-execution",
            content=(
                f"Plan '{plan_name}' execution finished: {done}/{total} tasks complete."
            ),
            details=details,
            plan_name=plan_name,
            tasks_completed=done,
            tasks_total=total,
        )

    # ── Worker delegation ────────────────────────────────────────────────────

    async def _run_worker(self, todo: dict, plan_name: str, notepad: Notepad,
                          category: str) -> str:
        """Run one Sisyphus-Junior worker for a single todo."""
        spec = get_category(category)
        agent = get_agent("sisyphus-junior")

        system = compose_worker_system(agent, spec, notepad.get_context_block())

        plan = self.boulder.active_plan
        prompt = (
            f"Active plan: {plan_name}\n"
            f"Plan file: {plan}\n\n"
            f"## Task\n{todo.get('title', '')}\n"
            f"{todo.get('detail', '')}\n\n"
            "Read the plan file for full context before acting. "
            "Complete the task, verify it, and report concisely what changed "
            "and how you verified it."
        )
        response = await self.engine.run([
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ])
        return response.content or ""

    @staticmethod
    def _extract_learning(result_text: str) -> str:
        """Pull a one-line learning from a worker report, if any."""
        for line in result_text.splitlines():
            line = line.strip()
            if line.lower().startswith(("learning", "learned", "note:")):
                return line
        return ""
