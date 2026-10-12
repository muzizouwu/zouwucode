"""Session sharing \u2014 render a session as a portable, self-contained document.

opencode has ``/share`` (it uploads to a service). ZOUWUCODE is local-first:
we never upload anything. Instead a session is rendered to Markdown and written
under ``<data_dir>/shares/`` so it can be pasted into an issue, attached to a
PR, dropped in a chat, or committed next to the code \u2014 no network, no account.
"""

import re
import time
from pathlib import Path


def _fmt_ts(ts) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except Exception:  # noqa: BLE001 \u2014 a bad timestamp must not break export
        return ""


def render_session_markdown(data: dict, title: str = "") -> str:
    """Render one session dict (as stored by SessionManager) to Markdown."""
    data = data or {}
    sid = data.get("session_id", "session")
    root = data.get("project_root", "")
    turns = data.get("turns", []) or []
    lines = [
        f"# {title or ('ZOUWUCODE session ' + str(sid))}",
        "",
        f"- **session**: `{sid}`",
    ]
    if root:
        lines.append(f"- **workspace**: `{root}`")
    started, updated = _fmt_ts(data.get("started_at")), _fmt_ts(data.get("updated_at"))
    if started:
        lines.append(f"- **started**: {started}")
    if updated:
        lines.append(f"- **updated**: {updated}")
    lines.append(f"- **turns**: {len(turns)}")
    lines += ["", "---", ""]
    for t in turns:
        role = str(t.get("role") or "turn").upper()
        ts = _fmt_ts(t.get("timestamp"))
        lines.append(f"### {role}" + (f" \u00b7 {ts}" if ts else ""))
        lines.append("")
        lines.append((t.get("content") or "").rstrip() or "_(empty)_")
        lines.append("")
    lines += ["---", "",
              "_Exported by ZOUWUCODE `/share` \u2014 local export, nothing uploaded._", ""]
    return "\n".join(lines)


def export_session(data: dict, out_dir, session_id: str = "", title: str = ""):
    """Write a session's Markdown to ``out_dir``; return the written Path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sid = session_id or (data or {}).get("session_id", "session")
    safe = re.sub(r"[^\w.-]", "-", str(sid))[:80] or "session"
    path = out_dir / f"{safe}.md"
    path.write_text(render_session_markdown(data, title=title), encoding="utf-8")
    return path
