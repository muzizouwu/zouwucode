"""Session manager — save, resume, fork, and rollback sessions.

Supports workspace rollback via side-git snapshots (doesn't touch
the project's own .git), inspired by DeepSeek-TUI's approach.
"""

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

from ..config import ZOUWUCODEConfig
from ..errors import describe_exc
from .snapshot import WorkspaceSnapshotter

logger = logging.getLogger("zouwucode.session")

_TITLE_MAX = 40


def derive_title(turns: list[dict]) -> str:
    """A readable label from the first real user turn (auto-naming).

    Sessions used to be listed by opaque ids; users could not tell them apart.
    Collapsing whitespace and capping the length keeps the sidebar tidy for
    CJK and Latin text alike.
    """
    for entry in turns or []:
        if entry.get("role") != "user":
            continue
        text = " ".join(str(entry.get("content") or "").split())
        if text:
            return text if len(text) <= _TITLE_MAX else text[: _TITLE_MAX - 1] + "…"
    return "新会话"


class SessionManager:
    """Manages session persistence, resumption, and rollback."""

    def __init__(self, config: ZOUWUCODEConfig):
        self.config = config
        self._sessions_dir = Path(config.data_dir) / "sessions"
        self._current_session_id: Optional[str] = None
        self._current_log: list[dict] = []
        self._current_title: str = ""
        # Workspace rollback (session.rollback_enabled): side-git snapshots
        # under <data_dir>/snapshots/<hash-of-workspace>.git, isolated from
        # the project's own .git.
        self._snapshotter: Optional[WorkspaceSnapshotter] = None
        # Per-message rewind: (message_index -> snapshot rev) checkpoints.
        self._checkpoints: list[dict] = []
        try:
            enabled = bool(getattr(config.session, "rollback_enabled", True))
            root = Path.cwd().resolve()
            import hashlib
            tag = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:16]
            self._snapshotter = WorkspaceSnapshotter(
                workspace=root,
                repo_dir=Path(config.data_dir) / "snapshots" / ("%s.git" % tag),
                enabled=enabled,
            )
        except Exception:  # never let rollback wiring break session startup
            self._snapshotter = None

    @property
    def snapshotter(self) -> Optional[WorkspaceSnapshotter]:
        return self._snapshotter

    def snapshot_workspace(self, label: str = "") -> Optional[str]:
        """Take a workspace snapshot (no-op when rollback is disabled)."""
        if self._snapshotter is None:
            return None
        return self._snapshotter.snapshot(label)

    def rollback_workspace(self, steps: int = 1) -> dict:
        """Undo the last ``steps`` turns' file changes (see WorkspaceSnapshotter)."""
        if self._snapshotter is None:
            return {"ok": False, "error": "workspace rollback is unavailable"}
        return self._snapshotter.rollback(steps)

    def list_snapshots(self, limit: int = 20) -> list:
        if self._snapshotter is None:
            return []
        return self._snapshotter.list_snapshots(limit)

    # ── Per-message rewind (Claude Code /rewind, Cline checkpoints) ──────────

    def record_checkpoint(self, index: Optional[int] = None) -> None:
        """Bind the current snapshot HEAD to a message index (default: log end)."""
        if self._snapshotter is None or not self._snapshotter.enabled:
            return
        rev = self._snapshotter.head()
        if not rev:
            return
        if index is None:
            index = len(self._current_log)
        self._checkpoints = [c for c in self._checkpoints if c["index"] != index]
        self._checkpoints.append({"index": index, "rev": rev})
        self._checkpoints.sort(key=lambda c: c["index"])

    def list_checkpoints(self) -> list[dict]:
        """Message-index checkpoints, oldest first."""
        return list(self._checkpoints)

    def _checkpoint_rev_for(self, message_index: int) -> Optional[str]:
        """Snapshot rev at (or before) a message index; earliest as fallback."""
        cands = [c for c in self._checkpoints if c["index"] <= message_index]
        if cands:
            return cands[-1]["rev"]
        return self._checkpoints[0]["rev"] if self._checkpoints else None

    def rewind(self, message_index, files: bool = True,
               conversation: bool = True) -> dict:
        """Rewind to a MESSAGE index: restore files and/or the conversation.

        Cline's insight — "the cost of a mistake drops to nearly zero" — made
        operational: pick any point in the session, then restore the workspace
        (code), the turn log (chat), or both (default). ``files`` checks the
        workspace out of the checkpoint snapshot at/before the index;
        ``conversation`` truncates the turn log to the first ``message_index``
        messages.
        """
        log = self._current_log
        try:
            mi = int(message_index)
        except (TypeError, ValueError):
            return {"ok": False, "error": "message_index must be an integer"}
        if mi < 0 or mi > len(log):
            return {"ok": False,
                    "error": "message_index out of range (0..%d)" % len(log)}
        result = {"ok": True, "message_index": mi, "files": False,
                  "conversation": False, "files_total": len(log), "rev": None}
        if files and self._snapshotter is not None and self._snapshotter.enabled:
            rev = self._checkpoint_rev_for(mi)
            if rev:
                r = self._snapshotter.reset_to(rev)
                result["files"] = bool(r.get("ok"))
                result["rev"] = r.get("target")
                if not r.get("ok"):
                    result["file_error"] = r.get("error")
            else:
                result["file_error"] = "no checkpoint at or before this index"
        if conversation:
            self._current_log = log[:mi]
            self._checkpoints = [c for c in self._checkpoints if c["index"] <= mi]
            if self.config.session.save_enabled:
                self._save_session()
            result["conversation"] = True
        return result

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
        self._current_title = ""
        self._checkpoints = []
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        return self._current_session_id

    def log_turn(self, entry: dict) -> None:
        """Log a single turn to the current session."""
        entry["timestamp"] = time.time()
        self._current_log.append(entry)
        if self.config.session.save_enabled:
            # Snapshot AFTER the turn, so HEAD == "state after last turn":
            # /rollback n undoes the last n turns, and the checkpoint binds
            # this snapshot to the current message index for /rewind.
            if self._snapshotter is not None:
                role = entry.get("role", "turn")
                self._snapshotter.snapshot("turn: %s" % role)
                self.record_checkpoint(len(self._current_log))
            self._save_session()

    def _save_session(self) -> None:
        """Persist the current session to disk."""
        if not self._current_session_id:
            return
        path = self._sessions_dir / f"{self._current_session_id}.json"
        data = {
            "session_id": self._current_session_id,
            "title": self._current_title or derive_title(self._current_log),
            # Workspace this session belongs to — lets --resume pick this
            # project's session when data_dir is shared across projects.
            "project_root": str(Path.cwd().resolve()),
            "started_at": self._current_log[0]["timestamp"] if self._current_log else time.time(),
            "updated_at": time.time(),
            "turns": self._current_log,
            "checkpoints": self._checkpoints,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_session(self, session_id: str) -> Optional[list[dict]]:
        """Load a previous session."""
        path = self._sessions_dir / f"{session_id}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self._current_session_id = session_id
            self._current_log = data.get("turns", [])
            self._current_title = data.get("title") or derive_title(self._current_log)
            self._checkpoints = data.get("checkpoints", [])
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
                    "title": data.get("title") or derive_title(data.get("turns", [])),
                    "turns": len(data.get("turns", [])),
                    "updated": data.get("updated_at", 0),
                    "project_root": data.get("project_root", ""),
                })
            except Exception as exc:
                logger.warning("skipping unreadable session file %s: %s",
                               f.name, describe_exc(exc))
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

    # ── Archive / fork (GUI session management) ─────────────────────────────
    # Kept as separate files rather than a flag so the active list stays a
    # cheap glob and archiving is reversible by moving the file back.

    @property
    def _archive_dir(self) -> Path:
        return self._sessions_dir / "archive"

    def _read_session_file(self, path: Path) -> Optional[dict]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("unreadable session file %s: %s", path.name,
                           describe_exc(exc))
            return None

    @staticmethod
    def _summarize(data: dict) -> dict:
        return {
            "id": data.get("session_id", ""),
            "title": data.get("title") or derive_title(data.get("turns", [])),
            "turns": len(data.get("turns", [])),
            "updated": data.get("updated_at", 0),
            "project_root": data.get("project_root", ""),
        }

    def rename_session(self, session_id: str, title: str) -> bool:
        """Rename a session (active or archived). Empty title is rejected."""
        name = " ".join(str(title or "").split())
        if not name:
            return False
        name = name if len(name) <= _TITLE_MAX else name[: _TITLE_MAX - 1] + "…"
        for path in (self._sessions_dir / f"{session_id}.json",
                     self._archive_dir / f"{session_id}.json"):
            if not path.exists():
                continue
            data = self._read_session_file(path)
            if data is None:
                return False
            data["title"] = name
            data["updated_at"] = time.time()
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8")
            if session_id == self._current_session_id:
                self._current_title = name
            return True
        return False

    def archive_session(self, session_id: str) -> bool:
        """Move a session out of the active list (reversible)."""
        src = self._sessions_dir / f"{session_id}.json"
        if not src.exists():
            return False
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        src.replace(self._archive_dir / src.name)
        return True

    def unarchive_session(self, session_id: str) -> bool:
        """Move a session back into the active list."""
        src = self._archive_dir / f"{session_id}.json"
        if not src.exists():
            return False
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        src.replace(self._sessions_dir / src.name)
        return True

    def list_archived(self) -> list[dict]:
        """Archived sessions, most recently updated first."""
        if not self._archive_dir.exists():
            return []
        out = []
        for f in self._archive_dir.glob("*.json"):
            data = self._read_session_file(f)
            if data:
                out.append(self._summarize(data))
        out.sort(key=lambda s: s["updated"], reverse=True)
        return out

    def fork_session(self, session_id: str, at_turn: Optional[int] = None,
                     new_id: Optional[str] = None) -> Optional[dict]:
        """Copy a session (optionally truncated to ``at_turn``) as a new one.

        Forking is how a user explores "what if I had stopped here / taken the
        other branch" without destroying the original conversation.
        """
        data = self.get_session(session_id)
        if data is None:
            return None
        turns = list(data.get("turns", []))
        if at_turn is not None:
            try:
                cut = max(0, int(at_turn))
            except (TypeError, ValueError):
                return None
            turns = turns[:cut]
        sid = new_id or f"fork-{session_id[:24]}-{uuid.uuid4().hex[:6]}"
        now = time.time()
        forked = {
            "session_id": sid,
            "title": (data.get("title") or derive_title(turns)) + " · 分支",
            "project_root": data.get("project_root") or str(Path.cwd().resolve()),
            "started_at": turns[0].get("timestamp", now) if turns else now,
            "updated_at": now,
            "turns": turns,
            "checkpoints": [],
            "forked_from": session_id,
        }
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        (self._sessions_dir / f"{sid}.json").write_text(
            json.dumps(forked, ensure_ascii=False, indent=2), encoding="utf-8")
        return self._summarize(forked)

    def get_session(self, session_id: str) -> Optional[dict]:
        """Get full session data (archived sessions included)."""
        for path in (self._sessions_dir / f"{session_id}.json",
                     self._archive_dir / f"{session_id}.json"):
            if path.exists():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except Exception as exc:
                    logger.warning("unreadable session %s: %s", session_id,
                                   describe_exc(exc))
                    return None
        return None