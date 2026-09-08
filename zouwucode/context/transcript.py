"""Raw transcript management — never fully read, grepped for identifiers.

This is the third layer of the three-layer context system.
Raw transcripts are the complete history of the session but are
never fully loaded into context. Instead, they are grepped for
specific identifiers when needed.
"""

import re
from pathlib import Path
from typing import Optional


class TranscriptManager:
    """Manages raw session transcripts for reference.

    Transcripts are stored as append-only logs and are searched
    (grep-style) rather than loaded into context.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._transcripts_dir = data_dir / "transcripts"
        self._current_session: Optional[Path] = None

    def start_session(self, session_id: str) -> None:
        """Start a new transcript session file."""
        self._transcripts_dir.mkdir(parents=True, exist_ok=True)
        self._current_session = self._transcripts_dir / f"{session_id}.log"

    def append(self, entry: dict) -> None:
        """Append an entry to the current transcript."""
        if self._current_session is None:
            return
        import json
        line = json.dumps(entry, ensure_ascii=False)
        with open(self._current_session, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def grep(self, pattern: str, session_id: Optional[str] = None) -> list[str]:
        """Search transcripts for a pattern (grep-style).

        Never reads the full transcript — iterates line by line.
        """
        results = []
        search_files = []

        if session_id:
            path = self._transcripts_dir / f"{session_id}.log"
            if path.exists():
                search_files.append(path)
        else:
            search_files = sorted(self._transcripts_dir.glob("*.log"))

        for path in search_files:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        if re.search(pattern, line, re.IGNORECASE):
                            results.append(line.rstrip())
            except Exception:
                continue

        return results[:100]  # limit results

    def get_session_list(self) -> list[dict]:
        """List all available session transcripts."""
        self._transcripts_dir.mkdir(parents=True, exist_ok=True)
        sessions = []
        for f in sorted(self._transcripts_dir.glob("*.log")):
            stat = f.stat()
            sessions.append({
                "id": f.stem,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })
        return sessions

    def get_session_summary(self, session_id: str) -> Optional[str]:
        """Get a summary of a session (first and last few lines)."""
        path = self._transcripts_dir / f"{session_id}.log"
        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) <= 10:
                return "".join(lines)
            # Return first 5 and last 5
            return "".join(lines[:5]) + "\n...\n" + "".join(lines[-5:])
        except Exception:
            return None