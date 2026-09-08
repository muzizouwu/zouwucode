"""Planner — Prometheus, the strategic consultant (ported from oh-my-opencode).

Prometheus is READ-ONLY for code: it interviews the user, researches the
codebase, and writes a detailed step-by-step plan to `.omo/plans/{name}.md`.
Metis performs a mandatory gap analysis on the draft; in high-accuracy mode
Momus reviews the plan and can reject it, looping until approval.

    .omo/plans/
    └── {plan-name}.md     # Executable plan (checkboxes → Atlas tasks)
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from zouwucode.engine.loop import EngineLoop
from .agents import get_agent
from .boulder import make_todo

# Event callback: (event_type, message, payload)
EventCallback = Callable[[str, str, dict], None]

# Ask callback: given a prompt, returns the user's answers (async).
AskCallback = Callable[[str], Awaitable[str]]


def _slugify(text: str, max_len: int = 40) -> str:
    """Turn free text into a safe filename slug (keeps CJK characters)."""
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", text.strip(), flags=re.UNICODE).strip("-")
    slug = slug[:max_len].strip("-")
    return slug or f"plan-{int(time.time())}"


class Planner:
    """Prometheus — strategic planner. Writes only `.omo/plans/*.md`."""

    def __init__(
        self,
        engine: EngineLoop,
        omo_dir: Path,
        on_event: Optional[EventCallback] = None,
    ):
        self.engine = engine
        self.omo_dir = Path(omo_dir).resolve()
        self.plans_dir = self.omo_dir / "plans"
        self.on_event = on_event or (lambda *_: None)

    def _emit(self, etype: str, message: str, payload: Optional[dict] = None) -> None:
        self.on_event(etype, message, payload or {})

    # ── Latest plan ──────────────────────────────────────────────────────────

    def latest_plan(self) -> Optional[Path]:
        """Return the most recently modified plan file, or None."""
        if not self.plans_dir.exists():
            return None
        plans = [p for p in self.plans_dir.glob("*.md") if p.is_file()]
        if not plans:
            return None
        return max(plans, key=lambda p: p.stat().st_mtime)

    # ── Plan creation ────────────────────────────────────────────────────────

    async def create_plan(
        self,
        task: str,
        interactive: bool = True,
        high_accuracy: bool = False,
        metis: Optional[Callable] = None,
        momus: Optional[Callable] = None,
        ask: Optional[AskCallback] = None,
        max_review_rounds: int = 2,
    ) -> Optional[Path]:
        """Interview → research → draft → Metis → (Momus) → write plan.

        Returns the plan file path, or None if planning failed.
        """
        self._emit("plan", f"Prometheus is planning: {task[:80]}")

        # 1. Interview — gather clarifying answers when an interactive
        #    channel is available; otherwise plan autonomously.
        context = task
        if interactive and ask is not None:
            questions = await self._generate_questions(task)
            self._emit("plan", "Prometheus interview:\n" + questions)
            answers = await ask(questions)
            if answers and answers.strip():
                context = f"{task}\n\n## Interview answers\n{answers.strip()}"
        elif interactive:
            self._emit("plan", "No interactive channel — planning autonomously.")

        # 2. Draft the plan with the Prometheus agent.
        system = get_agent("prometheus").system_prompt
        draft = await self._generate(context, system)
        if not draft or not draft.strip():
            self._emit("error", "Prometheus produced an empty plan.")
            return None

        # 3. Metis gap analysis (mandatory).
        if metis is not None:
            self._emit("plan", "Running Metis gap analysis...")
            feedback = await metis(task, draft)
            if feedback and feedback.strip():
                self._emit("plan", "Metis found gaps — revising draft.")
                draft = await self._revise(draft, feedback, system)

        # 4. Momus dual review (high-accuracy mode only).
        if high_accuracy and momus is not None:
            for round_no in range(max_review_rounds):
                self._emit("plan", f"Momus review (round {round_no + 1})...")
                verdict = await momus(task, draft)
                if verdict and "REJECT" in verdict.upper():
                    self._emit("plan", "Momus rejected — revising plan.")
                    draft = await self._revise(draft, verdict, system)
                else:
                    self._emit("plan", "Momus approved the plan.")
                    break

        # 5. Persist the plan.
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        name = _slugify(task)
        path = self.plans_dir / f"{name}.md"
        path.write_text(draft.strip() + "\n", encoding="utf-8")
        self._emit("plan", f"Plan written to {path.name}", {"path": str(path)})
        return path

    # ── LLM helpers ──────────────────────────────────────────────────────────

    async def _generate(self, user_text: str, system_prompt: str) -> str:
        """Run Prometheus once and return its content."""
        response = await self.engine.run([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ])
        return self._extract_plan_markdown(response.content or "")

    async def _generate_questions(self, task: str) -> str:
        """Ask Prometheus for clarifying interview questions."""
        system = get_agent("prometheus").system_prompt
        prompt = (
            f"The user wants: {task}\n\n"
            "List up to 5 clarifying questions to pin down scope, "
            "acceptance criteria, and technical approach. Number them."
        )
        response = await self.engine.run([
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ])
        return response.content or ""

    async def _revise(self, draft: str, feedback: str, system_prompt: str) -> str:
        """Revise a draft plan, addressing every reviewer point."""
        prompt = (
            "Revise the plan below, addressing EVERY point in the feedback. "
            "Keep the same markdown format.\n\n"
            f"## Current plan\n{draft}\n\n## Feedback\n{feedback}"
        )
        response = await self.engine.run([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ])
        revised = self._extract_plan_markdown(response.content or "")
        return revised or draft  # never lose a plan to a bad revision

    @staticmethod
    def _extract_plan_markdown(content: str) -> str:
        """Strip code fences if the model wrapped the plan in them."""
        m = re.search(r"```(?:markdown|md)?\s*\n(.*?)```", content, re.DOTALL)
        if m:
            return m.group(1).strip()
        # Also strip a leading "Here is the plan:" style preamble.
        return re.sub(r"^(?:here's?|here is|below is)[^:\n]*:\s*", "", content,
                      flags=re.IGNORECASE).strip()

    # ── Plan parsing ─────────────────────────────────────────────────────────

    def parse_plan_todos(self, plan_path: Path) -> list[dict]:
        """Parse checkbox tasks (`- [ ]` / `1. [ ]` / `- [x]`) from a plan.

        Returns a list of todo dicts suitable for the boulder.
        """
        if not Path(plan_path).exists():
            return []
        lines = Path(plan_path).read_text(encoding="utf-8").splitlines()
        pattern = re.compile(r"^\s*(?:[-*+]|\d+\.)\s*\[[ xX]\]\s*(.+)$")
        todos = []
        for line in lines:
            m = pattern.match(line)
            if m:
                todos.append(make_todo(m.group(1).strip()))
        return todos
