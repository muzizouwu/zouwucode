"""Skills Manager — loads .md rule files and skill packs from .zouwucode/.

File layout:
  .zouwucode/
    ├── rules.md              # Project rules (always loaded)
    └── skills/
        ├── python-dev.md     # Python development conventions
        ├── react-dev.md      # React component conventions
        └── ...               # Any .md file = one skill
"""

from pathlib import Path
from typing import Optional


class RuleLoader:
    """Loads and manages the project-level .zouwucode/rules.md file."""

    def __init__(self, project_root: Optional[Path] = None):
        self._project_root = (project_root or Path.cwd()).resolve()
        self._rules_path = self._project_root / ".zouwucode" / "rules.md"

    @property
    def rules_path(self) -> Path:
        return self._rules_path

    def exists(self) -> bool:
        return self._rules_path.exists()

    def load(self) -> str:
        """Read and return the rules.md content, or empty string."""
        if not self._rules_path.exists():
            return ""
        return self._rules_path.read_text(encoding="utf-8").strip()

    def save(self, content: str) -> None:
        """Write content to rules.md."""
        self._rules_path.parent.mkdir(parents=True, exist_ok=True)
        self._rules_path.write_text(content, encoding="utf-8")

    def delete(self) -> None:
        """Remove the rules.md file."""
        if self._rules_path.exists():
            self._rules_path.unlink()

    def get_context_block(self) -> str:
        """Return the rules block formatted for the system prompt."""
        content = self.load()
        if not content:
            return ""
        return f"<project_rules>\n{content}\n</project_rules>"


class SkillsManager:
    """Manages skill packs loaded from .zouwucode/skills/*.md.

    Skills are user-defined .md files that each describe a specific
    capability, convention, or workflow. They can be loaded/unloaded
    at runtime via the /skill command.
    """

    def __init__(self, project_root: Optional[Path] = None):
        self._project_root = (project_root or Path.cwd()).resolve()
        self._skills_dir = self._project_root / ".zouwucode" / "skills"
        self._loaded: dict[str, str] = {}  # skill_name -> content

    # ── Discovery ────────────────────────────────────────────────────────────

    @property
    def skills_dir(self) -> Path:
        return self._skills_dir

    def list_available(self) -> list[str]:
        """Return all .md filenames (without extension) in the skills dir."""
        if not self._skills_dir.exists():
            return []
        return sorted(
            f.stem
            for f in self._skills_dir.iterdir()
            if f.suffix == ".md" and f.is_file()
        )

    def list_loaded(self) -> list[str]:
        """Return names of currently loaded skills."""
        return sorted(self._loaded.keys())

    # ── Load / Unload ────────────────────────────────────────────────────────

    def load(self, name: str) -> str:
        """Load a skill by name (without .md extension). Returns content."""
        path = self._skills_dir / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(
                f"Skill '{name}' not found. Available: {', '.join(self.list_available()) or '(none)'}"
            )
        content = path.read_text(encoding="utf-8").strip()
        self._loaded[name] = content
        return content

    def unload(self, name: str) -> bool:
        """Unload a skill by name. Returns True if it was loaded."""
        return self._loaded.pop(name, None) is not None

    def unload_all(self) -> None:
        """Unload all skills."""
        self._loaded.clear()

    # ── Context ──────────────────────────────────────────────────────────────

    def get_context_block(self) -> str:
        """Return all loaded skills formatted for the system prompt."""
        if not self._loaded:
            return ""
        blocks = []
        for name, content in sorted(self._loaded.items()):
            blocks.append(f"<skill name=\"{name}\">\n{content}\n</skill>")
        return "\n".join(blocks)

    def get_summary(self) -> str:
        """Return a human-readable summary of skills state."""
        available = self.list_available()
        loaded = self.list_loaded()
        lines = [
            f"Skills directory: {self._skills_dir}",
            f"Available: {', '.join(available) or '(none)'}",
            f"Loaded ({len(loaded)}): {', '.join(loaded) or '(none)'}",
        ]
        return "\n".join(lines)