"""Cross-session messaging \u2014 a tiny local bus so independent ZOUWUCODE
sessions (any frontend, any terminal, one machine) can talk to each other.

Backed by SQLite (stdlib): concurrent processes coordinate through the database
itself, so there is no daemon, no port, and nothing to keep running. A session
addresses another by its agent name (``/peers`` lists them); ``/msg * text``
broadcasts. This is the "agents can collaborate across sessions" half of the
roadmap \u2014 the in-process ``task`` tool already covers fan-out inside one run.
"""

import os
import sqlite3
import time
import uuid
from pathlib import Path


class MessageBus:
    """A local, process-safe message bus (SQLite-backed)."""

    def __init__(self, db_path, agent: str = "", role: str = ""):
        self.db_path = str(db_path)
        self.agent = agent or f"agent-{uuid.uuid4().hex[:6]}"
        self.role = role
        self._init()

    # \u2500\u2500 storage \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS messages("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, sender TEXT, "
                "recipient TEXT, text TEXT, thread TEXT, read INTEGER DEFAULT 0)")
            c.execute(
                "CREATE TABLE IF NOT EXISTS agents("
                "agent TEXT PRIMARY KEY, role TEXT, pid INTEGER, last_seen REAL)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_inbox "
                      "ON messages(recipient, read)")

    # \u2500\u2500 presence \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    def register(self, role: str = None) -> str:
        """Announce this session (upsert) and refresh its last-seen time."""
        with self._connect() as c:
            c.execute(
                "INSERT INTO agents(agent, role, pid, last_seen) VALUES(?,?,?,?) "
                "ON CONFLICT(agent) DO UPDATE SET role=excluded.role, "
                "pid=excluded.pid, last_seen=excluded.last_seen",
                (self.agent, role if role is not None else self.role,
                 os.getpid(), time.time()))
        return self.agent

    def peers(self, exclude_self: bool = True, stale_after: float = 3600.0) -> list:
        """Other recently-active sessions on this machine (stale ones dropped)."""
        now = time.time()
        with self._connect() as c:
            rows = c.execute("SELECT * FROM agents ORDER BY last_seen DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if exclude_self and d.get("agent") == self.agent:
                continue
            if stale_after and (now - (d.get("last_seen") or 0)) > stale_after:
                continue
            with self._connect() as c:
                d["unread"] = c.execute(
                    "SELECT COUNT(*) FROM messages WHERE sender=? AND recipient=? AND read=0",
                    (d["agent"], self.agent)).fetchone()[0]
            out.append(d)
        return out

    # \u2500\u2500 messages \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    def send(self, recipient: str, text: str, sender: str = None,
             thread: str = None) -> int:
        """Send ``text`` to ``recipient`` (``*`` or empty = broadcast). Return id."""
        with self._connect() as c:
            cur = c.execute(
                "INSERT INTO messages(ts, sender, recipient, text, thread, read) "
                "VALUES(?,?,?,?,?,0)",
                (time.time(), sender or self.agent, recipient or "*",
                 str(text), thread))
            return cur.lastrowid

    def inbox(self, agent: str = None, unread_only: bool = True,
              limit: int = 50) -> list:
        """Messages addressed to this agent (directly or broadcast), oldest first."""
        agent = agent or self.agent
        q = ("SELECT * FROM messages WHERE (recipient=? OR recipient='*') "
             "AND sender!=?")
        args = [agent, agent]
        if unread_only:
            q += " AND read=0"
        q += " ORDER BY id ASC LIMIT ?"
        args.append(int(limit))
        with self._connect() as c:
            rows = c.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    def mark_read(self, ids) -> int:
        ids = [int(i) for i in (ids or [])]
        if not ids:
            return 0
        with self._connect() as c:
            c.executemany("UPDATE messages SET read=1 WHERE id=?", [(i,) for i in ids])
        return len(ids)

    def drain(self, agent: str = None) -> list:
        """Return unread inbox messages AND mark them read (the common case)."""
        msgs = self.inbox(agent=agent, unread_only=True)
        self.mark_read([m["id"] for m in msgs])
        return msgs


def format_inbox(msgs: list) -> str:
    """Human-readable rendering shared by the three frontends."""
    if not msgs:
        return "  (inbox empty)"
    lines = []
    for m in msgs:
        ts = time.strftime("%H:%M:%S", time.localtime(m.get("ts") or 0))
        to = m.get("recipient") or "*"
        lines.append(f"  [{m.get('id')}] {ts}  {m.get('sender')} \u2192 {to}: {m.get('text')}")
    return "\n".join(lines)


def format_peers(peers: list) -> str:
    if not peers:
        return "  (no other sessions seen recently)"
    lines = []
    for p in peers:
        ts = time.strftime("%H:%M:%S", time.localtime(p.get("last_seen") or 0))
        lines.append(f"  {p.get('agent')}  role={p.get('role') or '-'}  "
                     f"pid={p.get('pid')}  last={ts}  unread={p.get('unread', 0)}")
    return "\n".join(lines)
