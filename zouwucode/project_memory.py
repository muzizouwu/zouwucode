"""Persistent Project Memory — cross-session state save/restore.

Stores project state as JSON files in .zouwucode/ directory within the
project root, so it travels with the project and survives reboots.
"""

import json
import time
import hashlib
from pathlib import Path
from typing import Any, Optional


class ProjectMemory:
    """Persistent project memory that survives restarts.

    Stores:
    - Recent files and their last-modified timestamps
    - Project structure snapshot (key directories and files)
    - Conversation summaries from previous sessions
    - Key decisions made during development
    - Current task / goal state
    """

    def __init__(self, project_root: Optional[Path] = None):
        self._project_root = Path(project_root).resolve() if project_root else Path.cwd().resolve()
        self._memory_dir = self._project_root / ".zouwucode" / "memory"
        self._state_file = self._memory_dir / "project_state.json"
        self._decisions_file = self._memory_dir / "decisions.json"
        self._summaries_file = self._memory_dir / "summaries.json"
        self._goals_file = self._memory_dir / "goals.json"
        self._cache: dict[str, Any] = {}
        self._dirty = False

    # ── Initialization ──────────────────────────────────────────────────────

    def ensure_dir(self) -> None:
        self._memory_dir.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> dict:
        """Load all persistent state from disk."""
        self.ensure_dir()
        return {
            "state": self._load_json(self._state_file),
            "decisions": self._load_json(self._decisions_file),
            "summaries": self._load_json(self._summaries_file),
            "goals": self._load_json(self._goals_file),
        }

    def save_all(self) -> None:
        """Flush all cached state to disk."""
        if not self._dirty:
            return
        self.ensure_dir()
        self._save_json(self._state_file, self._cache.get("state", {}))
        self._save_json(self._decisions_file, self._cache.get("decisions", []))
        self._save_json(self._summaries_file, self._cache.get("summaries", []))
        self._save_json(self._goals_file, self._cache.get("goals", []))
        self._dirty = False

    # ── Project State ───────────────────────────────────────────────────────

    def get_state(self, key: str, default: Any = None) -> Any:
        data = self._cache.setdefault("state", self._load_json(self._state_file))
        return data.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        data = self._cache.setdefault("state", self._load_json(self._state_file))
        data[key] = value
        self._dirty = True

    def update_state(self, mapping: dict) -> None:
        data = self._cache.setdefault("state", self._load_json(self._state_file))
        data.update(mapping)
        self._dirty = True

    # ── Decisions ────────────────────────────────────────────────────────────

    def add_decision(self, title: str, detail: str, category: str = "general") -> None:
        decisions = self._cache.setdefault("decisions", self._load_json(self._decisions_file))
        decisions.append({
            "id": hashlib.md5(f"{title}{time.time()}".encode()).hexdigest()[:8],
            "title": title,
            "detail": detail,
            "category": category,
            "timestamp": time.time(),
        })
        self._dirty = True

    def get_decisions(self, category: Optional[str] = None, limit: int = 20) -> list[dict]:
        decisions = self._cache.setdefault("decisions", self._load_json(self._decisions_file))
        if category:
            decisions = [d for d in decisions if d.get("category") == category]
        return sorted(decisions, key=lambda d: d.get("timestamp", 0), reverse=True)[:limit]

    def get_decisions_context(self, limit: int = 10) -> str:
        """Return decisions as a formatted context block for the LLM prompt."""
        decisions = self.get_decisions(limit=limit)
        if not decisions:
            return ""
        lines = ["## Previous Decisions", ""]
        for d in decisions:
            lines.append(f"- [{d.get('category', 'general')}] {d['title']}: {d['detail']}")
        return "\n".join(lines)

    # ── Conversation Summaries ───────────────────────────────────────────────

    def add_summary(self, session_id: str, summary: str, turn_count: int) -> None:
        summaries = self._cache.setdefault("summaries", self._load_json(self._summaries_file))
        # Keep last 50 summaries, drop oldest if over limit
        summaries.append({
            "session_id": session_id,
            "summary": summary,
            "turn_count": turn_count,
            "timestamp": time.time(),
        })
        if len(summaries) > 50:
            summaries[:] = summaries[-50:]
        self._dirty = True

    def get_summaries(self, limit: int = 5) -> list[dict]:
        summaries = self._cache.setdefault("summaries", self._load_json(self._summaries_file))
        return sorted(summaries, key=lambda s: s.get("timestamp", 0), reverse=True)[:limit]

    def get_summaries_context(self, limit: int = 3) -> str:
        """Return past session summaries as a context block."""
        summaries = self.get_summaries(limit=limit)
        if not summaries:
            return ""
        lines = ["## Past Session Summaries", ""]
        for s in summaries:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(s.get("timestamp", 0)))
            lines.append(f"- [{ts}] ({s.get('turn_count', 0)} turns) {s['summary']}")
        return "\n".join(lines)

    # ── Goals / Tasks ────────────────────────────────────────────────────────

    def set_goal(self, goal: str) -> None:
        self.set_state("current_goal", goal)
        goals = self._cache.setdefault("goals", self._load_json(self._goals_file))
        goals.append({
            "goal": goal,
            "status": "active",
            "created": time.time(),
        })
        self._dirty = True

    def complete_goal(self, goal: str) -> None:
        goals = self._cache.setdefault("goals", self._load_json(self._goals_file))
        for g in goals:
            if g["goal"] == goal and g["status"] == "active":
                g["status"] = "completed"
                g["completed_at"] = time.time()
                break
        self.set_state("current_goal", "")
        self._dirty = True

    def get_active_goal(self) -> str:
        return self.get_state("current_goal", "")

    def get_goal_context(self) -> str:
        """Return goal info as a context block."""
        goal = self.get_active_goal()
        if not goal:
            return ""
        return f"## Current Goal\n\n{goal}\n"

    # ── Project Snapshot ────────────────────────────────────────────────────

    def snapshot_project(self) -> dict:
        """Take a lightweight snapshot of the project structure."""
        root = self._project_root
        snapshot = {
            "root": str(root),
            "key_files": [],
            "key_dirs": [],
            "file_count": 0,
            "dir_count": 0,
        }

        # Only scan top 2 levels to keep it fast
        try:
            for entry in sorted(root.iterdir()):
                if entry.name.startswith(".") or entry.name.startswith("__"):
                    continue
                if entry.is_dir():
                    snapshot["key_dirs"].append(entry.name)
                    snapshot["dir_count"] += 1
                    # Scan one level deeper
                    for sub in sorted(entry.iterdir()):
                        if sub.is_file() and not sub.name.startswith("."):
                            snapshot["key_files"].append(f"{entry.name}/{sub.name}")
                            snapshot["file_count"] += 1
                            if snapshot["file_count"] >= 50:
                                break
                elif entry.is_file():
                    snapshot["key_files"].append(entry.name)
                    snapshot["file_count"] += 1
        except PermissionError:
            pass

        return snapshot

    def get_project_context(self) -> str:
        """Return project context block for the LLM prompt."""
        state = self.get_state("description", "")
        snapshot = self.snapshot_project()

        lines = ["## Project Context", ""]
        if state:
            lines.append(f"Description: {state}")
        lines.append(f"Root: {snapshot['root']}")
        lines.append(f"Files: {snapshot['file_count']}, Dirs: {snapshot['dir_count']}")
        if snapshot["key_dirs"]:
            lines.append(f"Directories: {', '.join(snapshot['key_dirs'][:10])}")
        lines.append("")

        return "\n".join(lines)

    # ── Utility ──────────────────────────────────────────────────────────────

    def get_full_context(self) -> str:
        """Get ALL context blocks combined for the LLM prompt."""
        blocks = [
            self.get_project_context(),
            self.get_goal_context(),
            self.get_decisions_context(),
            self.get_summaries_context(),
        ]
        return "\n\n".join(b for b in blocks if b)

    def _load_json(self, path: Path) -> Any:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, Exception):
                return {} if path.name.endswith("state.json") else []
        return {} if path.name.endswith("state.json") else []

    def _save_json(self, path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")