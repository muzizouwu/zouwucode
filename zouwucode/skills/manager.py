"""Skills Manager — loads .md rule files and skill packs from .zouwucode/.

File layout:
  .zouwucode/
    ├── rules.md              # Project rules (always loaded)
    └── skills/
        ├── python-dev.md     # Python development conventions
        ├── react-dev.md      # React component conventions
        └── ...               # Any .md file = one skill

Also reads AGENTS.md at the project root (the cross-tool open format used by
Codex CLI, Cursor, Gemini CLI, Copilot, …) so a repository's existing agent
instructions work with ZOUWUCODE without extra setup.
"""

from pathlib import Path
from typing import Optional

# Codex CLI's default project_doc_max_bytes: large enough for real project
# docs, small enough that a runaway file cannot blow up the frozen prefix.
_MAX_AGENTS_MD_BYTES = 32_768


class RuleLoader:
    """Loads project instructions: `.zouwucode/rules.md` + `AGENTS.md`."""

    def __init__(self, project_root: Optional[Path] = None):
        self._project_root = (project_root or Path.cwd()).resolve()
        self._rules_path = self._project_root / ".zouwucode" / "rules.md"
        self._agents_path = self._project_root / "AGENTS.md"

    @property
    def rules_path(self) -> Path:
        return self._rules_path

    @property
    def agents_path(self) -> Path:
        return self._agents_path

    def exists(self) -> bool:
        return self._rules_path.exists() or self._agents_path.exists()

    def _read_agents_md(self) -> str:
        if not self._agents_path.is_file():
            return ""
        try:
            raw = self._agents_path.read_bytes()[:_MAX_AGENTS_MD_BYTES]
            return raw.decode("utf-8", "replace").strip()
        except OSError:
            return ""

    def load(self) -> str:
        """Read and return all project instructions, or empty string."""
        parts = []
        if self._rules_path.exists():
            parts.append(self._rules_path.read_text(encoding="utf-8").strip())
        agents = self._read_agents_md()
        if agents:
            parts.append(agents)
        return "\n\n".join(p for p in parts if p)

    def save(self, content: str) -> None:
        """Write content to rules.md."""
        self._rules_path.parent.mkdir(parents=True, exist_ok=True)
        self._rules_path.write_text(content, encoding="utf-8")

    def delete(self) -> None:
        """Remove the rules.md file."""
        if self._rules_path.exists():
            self._rules_path.unlink()

    def get_context_block(self) -> str:
        """Return project instructions formatted for the system prompt."""
        blocks = []
        if self._rules_path.exists():
            own = self._rules_path.read_text(encoding="utf-8").strip()
            if own:
                blocks.append(f"<project_rules>\n{own}\n</project_rules>")
        agents = self._read_agents_md()
        if agents:
            blocks.append(f"<agents_md>\n{agents}\n</agents_md>")
        return "\n\n".join(blocks)


class SkillsManager:
    """Manages skill packs loaded from .zouwucode/skills/*.md.

    Skills are user-defined .md files that each describe a specific
    capability, convention, or workflow. They can be loaded/unloaded
    at runtime via the /skill command.
    """

    def __init__(self, project_root: Optional[Path] = None):
        self._project_root = (project_root or Path.cwd()).resolve()
        self._skills_dir = self._project_root / ".zouwucode" / "skills"
        # User-level skills (~/.zouwucode/skills) are shared by every project,
        # matching how other agents ship reusable "skills" libraries.
        self._global_dir = Path.home() / ".zouwucode" / "skills"
        self._loaded: dict[str, str] = {}  # skill_name -> content

    # ── Discovery ────────────────────────────────────────────────────────────

    @property
    def skills_dir(self) -> Path:
        return self._skills_dir

    @property
    def global_dir(self) -> Path:
        return self._global_dir

    def _search_dirs(self) -> list[Path]:
        """Project skills first (they shadow global ones), then user-level."""
        return [self._skills_dir, self._global_dir]

    def list_available(self) -> list[str]:
        """Return every available skill name from project + user directories."""
        names: dict[str, None] = {}
        for directory in self._search_dirs():
            if not directory.exists():
                continue
            for f in sorted(directory.iterdir()):
                if f.suffix == ".md" and f.is_file():
                    names.setdefault(f.stem, None)
        return sorted(names)

    def list_loaded(self) -> list[str]:
        """Return names of currently loaded skills."""
        return sorted(self._loaded.keys())

    # ── Load / Unload ────────────────────────────────────────────────────────

    def load(self, name: str) -> str:
        """Load a skill by name (project first, then user-level). Returns content."""
        path = next(
            (d / f"{name}.md" for d in self._search_dirs()
             if (d / f"{name}.md").is_file()),
            None,
        )
        if path is None:
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
            f"User skills: {self._global_dir}",
            f"Available: {', '.join(available) or '(none)'}",
            f"Loaded ({len(loaded)}): {', '.join(loaded) or '(none)'}",
        ]
        return "\n".join(lines)