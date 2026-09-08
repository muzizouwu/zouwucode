"""Notepad system — wisdom accumulation across tasks (ported from oh-my-opencode).

After each delegated task, the orchestrator extracts learnings and writes
them to a per-plan notepad so every subsequent worker benefits from what
earlier workers learned:

    .omo/notepads/{plan-name}/
    ├── learnings.md      # Patterns, conventions, successful approaches
    ├── decisions.md      # Architectural choices and rationales
    ├── issues.md         # Problems, blockers, gotchas encountered
    ├── verification.md   # Test results, validation outcomes
    └── problems.md       # Unresolved issues, technical debt
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

NOTEPAD_FILES: tuple[str, ...] = (
    "learnings.md",
    "decisions.md",
    "issues.md",
    "verification.md",
    "problems.md",
)


class Notepad:
    """Per-plan learning notebook persisted under `.omo/notepads/{plan}/`."""

    def __init__(self, plan_name: str, omo_dir: Path):
        """Create a notepad for a plan, rooted at `.omo`."""
        safe = _safe_name(plan_name)
        self.plan_name = plan_name
        self._dir = omo_dir / "notepads" / safe
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def directory(self) -> Path:
        return self._dir

    # ── File access ──────────────────────────────────────────────────────────

    def read(self, section: str) -> str:
        """Read a section file (learnings/decisions/...)."""
        path = self._dir / f"{section}.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    def write(self, section: str, content: str) -> None:
        """Overwrite a section file."""
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / f"{section}.md").write_text(content, encoding="utf-8")

    def append(self, section: str, entry: str) -> None:
        """Append a dated entry to a section file."""
        import time
        stamp = time.strftime("%Y-%m-%d %H:%M")
        current = self.read(section)
        line = f"- [{stamp}] {entry.strip()}"
        self.write(section, f"{current}\n{line}" if current else line)

    # ── Structured helpers ───────────────────────────────────────────────────

    def add_learning(self, text: str) -> None:
        """Record a convention / pattern / successful approach."""
        self.append("learnings", text)

    def add_decision(self, decision: str, rationale: str = "") -> None:
        """Record an architectural decision."""
        entry = decision if not rationale else f"{decision} — {rationale}"
        self.append("decisions", entry)

    def add_issue(self, text: str) -> None:
        """Record a problem / blocker / gotcha."""
        self.append("issues", text)

    def add_verification(self, text: str) -> None:
        """Record a test result / validation outcome."""
        self.append("verification", text)

    def add_problem(self, text: str) -> None:
        """Record an unresolved issue / technical debt."""
        self.append("problems", text)

    # ── Context assembly ─────────────────────────────────────────────────────

    def get_context_block(self, sections: Optional[list[str]] = None) -> str:
        """Assemble notepad content for injection into worker prompts."""
        sections = sections or ["learnings", "decisions", "issues", "verification"]
        blocks = []
        for sec in sections:
            content = self.read(sec)
            if content:
                blocks.append(f"<notepad section=\"{sec}\">\n{content}\n</notepad>")
        return "\n".join(blocks)

    def summary(self) -> str:
        """Human-readable notepad summary."""
        lines = [f"Notepad: {self.plan_name}"]
        for sec in NOTEPAD_FILES:
            content = self.read(sec)
            count = len([l for l in content.splitlines() if l.startswith("- [")])
            lines.append(f"  {sec}: {count} entries")
        return "\n".join(lines)


def _safe_name(name: str) -> str:
    """Sanitize a plan name into a safe directory name."""
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name).strip("-") or "plan"
