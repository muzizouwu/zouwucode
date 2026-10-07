"""SQLite-backed task queue for async dev-mode hosting (Devin-style backlog).

Tasks are submitted (`zouwucode dev --queue`), then executed by worker
processes (`zouwucode dev --workers N`) or a watcher (`--watch`). SQLite is
the coordination point — each task row transitions:

    pending → running → done | failed

Workers claim rows atomically with a single UPDATE ... WHERE status='pending'
guard, so parallel processes never grab the same task. Crash recovery: rows
stuck in 'running' past their timeout are requeued on startup.
"""

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger("zouwucode.dev.queue")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dev_tasks (
    id          TEXT PRIMARY KEY,
    task_ref    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending|running|done|failed
    result_json TEXT,
    error       TEXT,
    created_at  REAL NOT NULL,
    started_at  REAL,
    finished_at REAL,
    timeout     REAL DEFAULT 3600
);
CREATE INDEX IF NOT EXISTS idx_dev_tasks_status ON dev_tasks(status);
"""


class DevQueue:
    """Durable queue of dev tasks (one SQLite file per workspace)."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), timeout=30,
                                     isolation_level=None)  # autocommit
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    # ── Submit / claim ─────────────────────────────────────────────────────

    def submit(self, task_ref: str, timeout: Optional[float] = None) -> str:
        task_id = uuid.uuid4().hex[:12]
        self._conn.execute(
            "INSERT INTO dev_tasks (id, task_ref, created_at, timeout) "
            "VALUES (?, ?, ?, ?)",
            (task_id, task_ref, time.time(),
             timeout if timeout is not None else 3600),
        )
        logger.info("Queued dev task %s: %s", task_id, task_ref[:100])
        return task_id

    def claim_next(self) -> Optional[dict]:
        """Atomically claim the oldest pending task, or None."""
        cur = self._conn.execute(
            "UPDATE dev_tasks SET status='running', started_at=? "
            "WHERE id = ("
            "  SELECT id FROM dev_tasks WHERE status='pending' "
            "  ORDER BY created_at LIMIT 1"
            ") RETURNING id, task_ref, timeout",
            (time.time(),),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    def finish(self, task_id: str, *, success: bool,
               result: Optional[dict] = None, error: str = "") -> None:
        status = "done" if success else "failed"
        self._conn.execute(
            "UPDATE dev_tasks SET status=?, finished_at=?, result_json=?, error=? "
            "WHERE id=?",
            (status, time.time(),
             json.dumps(result, ensure_ascii=False) if result else None,
             error[:2000], task_id),
        )

    # ── Recovery / introspection ───────────────────────────────────────────

    def requeue_stale(self) -> int:
        """Requeue 'running' rows whose timeout elapsed (worker crashed)."""
        cur = self._conn.execute(
            "UPDATE dev_tasks SET status='pending', started_at=NULL "
            "WHERE status='running' AND started_at + timeout < ?",
            (time.time(),),
        )
        if cur.rowcount:
            logger.warning("Requeued %d stale task(s)", cur.rowcount)
        return cur.rowcount

    def stats(self) -> dict:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM dev_tasks GROUP BY status"
        ).fetchall()
        out = {r["status"]: r["n"] for r in rows}
        return {k: out.get(k, 0) for k in ("pending", "running", "done", "failed")}

    def list_tasks(self, limit: int = 20, status: Optional[str] = None) -> list[dict]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM dev_tasks WHERE status=? "
                "ORDER BY created_at DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM dev_tasks ORDER BY created_at DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
