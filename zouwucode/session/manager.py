"""Session manager — save, resume, fork, and rollback sessions.

Supports workspace rollback via side-git snapshots (doesn't touch
the project's own .git), inspired by DeepSeek-TUI's approach.
"""

import json
import time
import uuid
from pathlib import Path
from typing import Optional

from ..config import ZOUWUCODEConfig


class SessionManager:
    """Manages session persistence, resumption, and rollback."""

    def __init__(self, config: ZOUWUCODEConfig):
        self.config = config
        self._sessions_dir = Path(config.data_dir) / "sessions"
        self._current_session_id: Optional[str] = None
        self._current_log: list[dict] = []

    @property
    def current_session_id(self) -> Optional[str]:
        return self._current_session_id

    def current_log(self) -> list[dict]:
        """Turns logged so far in the current session (shallow copy)."""
        return list(self._current_log)

    def start_session(self, session_id: Optional[str] = None) -> str:
        """Start a new session."""
        self._current_session_id = session_id or f"session-{uuid.uuid4().hex[:12]}"
        self._current_log = []
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        return self._current_session_id

    def log_turn(self, entry: dict) -> None:
        """Log a single turn to the current session."""
        entry["timestamp"] = time.time()
        self._current_log.append(entry)
        if self.config.session.save_enabled:
            self._save_session()

    def _save_session(self) -> None:
        """Persist the current session to disk."""
        if not self._current_session_id:
            return
        path = self._sessions_dir / f"{self._current_session_id}.json"
        data = {
            "session_id": self._current_session_id,
            # Workspace this session belongs to — lets --resume pick this
            # project's session when data_dir is shared across projects.
            "project_root": str(Path.cwd().resolve()),
            "started_at": self._current_log[0]["timestamp"] if self._current_log else time.time(),
            "updated_at": time.time(),
            "turns": self._current_log,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_session(self, session_id: str) -> Optional[list[dict]]:
        """Load a previous session."""
        path = self._sessions_dir / f"{session_id}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self._current_session_id = session_id
            self._current_log = data.get("turns", [])
            return self._current_log
        return None

    def list_sessions(self) -> list[dict]:
        """List all available sessions, most recently updated first."""
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        sessions = []
        for f in self._sessions_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sessions.append({
                    "id": data["session_id"],
                    "turns": len(data.get("turns", [])),
                    "updated": data.get("updated_at", 0),
                    "project_root": data.get("project_root", ""),
                })
            except Exception:
                continue
        sessions.sort(key=lambda s: s["updated"], reverse=True)
        return sessions[:self.config.session.max_sessions]

    def latest_session(self, project_root: Optional[str] = None) -> Optional[dict]:
        """Most recently updated session, preferring this workspace's own.

        Sessions saved before project_root existed (or from other
        workspaces) act as a fallback, so --resume still finds something.
        """
        sessions = self.list_sessions()
        if not sessions:
            return None
        root = project_root or str(Path.cwd().resolve())
        same = [s for s in sessions if s.get("project_root") == root]
        return same[0] if same else sessions[0]

    @staticmethod
    def replay_history(entries: list[dict], cache, context_window) -> int:
        """Append a loaded session's turns back into the live engine context.

        freeze_session() seeds the PrefixCache with the system prompt only,
        so a resumed session MUST replay its user/assistant turns or the
        model would see a blank conversation while the UI looked restored.
        system markers (interrupt notes) and empty contents are skipped so
        the replayed prefix matches a normally-built one. Returns the number
        of messages replayed.
        """
        replayed = 0
        for entry in entries or []:
            role = entry.get("role")
            content = entry.get("content")
            if role not in ("user", "assistant") or not content:
                continue
            message = {"role": role, "content": content}
            cache.append(message)
            context_window.add_to_warm(message)
            replayed += 1
        return replayed

    def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        path = self._sessions_dir / f"{session_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def get_session(self, session_id: str) -> Optional[dict]:
        """Get full session data."""
        path = self._sessions_dir / f"{session_id}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None