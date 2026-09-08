"""Topic files — fetched on demand for actual project knowledge.

This is the second layer of the three-layer context system.
Topic files provide detailed project knowledge and are loaded
into context only when the model requests them.
"""

from pathlib import Path
from typing import Optional


class TopicManager:
    """Manages topic files that are loaded on demand.

    Topic files contain detailed information about specific aspects
    of the project (architecture, API docs, etc.) and are only
    loaded into context when the model explicitly requests them.
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self._topics_dir = workspace / ".zouwucode" / "topics"

    def ensure_dir(self) -> None:
        """Create topics directory if it doesn't exist."""
        self._topics_dir.mkdir(parents=True, exist_ok=True)

    def list_topics(self) -> list[dict]:
        """List all available topic files with metadata."""
        self.ensure_dir()
        topics = []
        for f in sorted(self._topics_dir.glob("*.md")):
            stat = f.stat()
            topics.append({
                "name": f.stem,
                "path": str(f),
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })
        return topics

    def get_topic(self, name: str) -> Optional[str]:
        """Read a specific topic file."""
        path = self._topics_dir / f"{name}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def set_topic(self, name: str, content: str) -> None:
        """Create or update a topic file."""
        self.ensure_dir()
        path = self._topics_dir / f"{name}.md"
        path.write_text(content, encoding="utf-8")

    def remove_topic(self, name: str) -> bool:
        """Remove a topic file."""
        path = self._topics_dir / f"{name}.md"
        if path.exists():
            path.unlink()
            return True
        return False

    def get_topic_context(self, topic_name: str) -> str:
        """Get the context block for a specific topic."""
        content = self.get_topic(topic_name)
        if content:
            return f"## Topic: {topic_name}\n\n{content}\n"
        return f"## Topic: {topic_name}\n\n(Topic not found)\n"