"""Boulder state — cross-session work continuity (ported from oh-my-opencode).

The `.omo/boulder.json` file tracks the active plan and per-task status so
work can be resumed across sessions via `/start-work`. Named after the
Sisyphus myth: the boulder keeps rolling no matter how many times it falls.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional


class BoulderState:
    """Persistent state for the active execution plan."""

    def __init__(self, omo_dir: Path):
        """Create a BoulderState rooted at the `.omo` directory."""
        self._omo_dir = omo_dir
        self._path = omo_dir / "boulder.json"
        self._data: dict = self._load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self) -> None:
        self._omo_dir.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return bool(self._data)

    def clear(self) -> None:
        """Reset boulder state."""
        self._data = {}
        if self._path.exists():
            self._path.unlink()

    # ── Accessors ────────────────────────────────────────────────────────────

    @property
    def active_plan(self) -> Optional[str]:
        return self._data.get("active_plan")

    @property
    def plan_name(self) -> Optional[str]:
        return self._data.get("plan_name")

    @property
    def started_at(self) -> Optional[float]:
        return self._data.get("started_at")

    @property
    def session_ids(self) -> list[str]:
        return self._data.get("session_ids", [])

    @property
    def todos(self) -> list[dict]:
        return self._data.get("todos", [])

    @property
    def progress(self) -> dict:
        todos = self.todos
        done = sum(1 for t in todos if t.get("status") == "done")
        return {"completed": done, "total": len(todos)}

    # ── Mutations ────────────────────────────────────────────────────────────

    def start(self, plan_name: str, plan_path: str, todos: list[dict],
              session_id: Optional[str] = None) -> None:
        """Start tracking a plan execution."""
        self._data = {
            "active_plan": plan_path,
            "plan_name": plan_name,
            "session_ids": [session_id] if session_id else [],
            "started_at": time.time(),
            "updated_at": time.time(),
            "todos": todos,
            "notepad": f"notepads/{plan_name}",
        }
        self.save()

    def add_session(self, session_id: str) -> None:
        if session_id and session_id not in self._data.get("session_ids", []):
            self._data.setdefault("session_ids", []).append(session_id)
            self._data["updated_at"] = time.time()
            self.save()

    def update_todo(self, task_id: str, status: str) -> bool:
        """Update a single todo's status (pending|in_progress|done|failed)."""
        for todo in self._data.get("todos", []):
            if todo.get("id") == task_id:
                todo["status"] = status
                self._data["updated_at"] = time.time()
                self.save()
                return True
        return False

    def set_todo_in_progress(self, task_id: str) -> bool:
        return self.update_todo(task_id, "in_progress")

    def complete_todo(self, task_id: str) -> bool:
        return self.update_todo(task_id, "done")

    def fail_todo(self, task_id: str) -> bool:
        return self.update_todo(task_id, "failed")

    def next_pending_todo(self) -> Optional[dict]:
        """Return the first non-done todo."""
        for todo in self._data.get("todos", []):
            if todo.get("status") != "done":
                return todo
        return None

    def remaining(self) -> list[dict]:
        """Return all not-done todos."""
        return [t for t in self._data.get("todos", []) if t.get("status") != "done"]

    def summary(self) -> str:
        """Human-readable progress summary."""
        if not self.exists():
            return "No active plan. Run /plan first, then /start-work."
        prog = self.progress
        name = self.plan_name or "(untitled)"
        return (
            f"Active plan: {name}\n"
            f"Progress: {prog['completed']}/{prog['total']} tasks complete\n"
            f"Started: {time.strftime('%Y-%m-%d %H:%M', time.localtime(self.started_at or 0))}\n"
            f"Sessions involved: {len(self.session_ids)}"
        )


def make_todo(title: str, detail: str = "", category: str = "deep") -> dict:
    """Create a todo dict for the boulder."""
    return {
        "id": f"task-{uuid.uuid4().hex[:8]}",
        "title": title,
        "detail": detail,
        "category": category,
        "status": "pending",
    }
