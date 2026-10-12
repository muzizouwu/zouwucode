"""Workspace rollback via side-git snapshots.

Backs the ``session.rollback_enabled`` feature SessionManager's docstring
has promised since the beginning: a coding agent should be able to undo the
file changes a turn made. Snapshots live in a *separate* git directory
(GIT_DIR) whose work tree is the workspace, so the project's own ``.git`` is
never read or written -- the two histories are fully isolated.

Only the standard ``git`` executable is used (no SDK). If git is missing, or
the feature is disabled, every method degrades to a safe no-op (``None`` /
``{"ok": False}``) instead of raising.

Rollback semantics: a snapshot is taken at the start of a session (baseline)
and after every completed turn, so ``HEAD`` is always "state after the last
turn". ``rollback(n)`` restores the workspace to ``HEAD~n`` -- i.e. undoes
the last n turns. Files *added* after the target snapshot are deleted;
modified/deleted files are checked out from the target. Untracked files that
were never part of any snapshot are left untouched (never destroy user data
the agent never saw).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# Paths that must never be snapshotted: the project's own VCS metadata,
# dependency/build caches, and the agent's own data/worktree dirs.
EXCLUDE_PATTERNS = (
    ".git/",
    "node_modules/",
    "__pycache__/",
    "*.pyc",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".venv/",
    "venv/",
    "env/",
    ".zouwucode_worktrees/",
    ".hello-my-zouwucode/",
    "zouwucode_data/",
)

_AUTHOR = {
    "GIT_AUTHOR_NAME": "zouwucode",
    "GIT_AUTHOR_EMAIL": "snapshot@zouwucode.local",
    "GIT_COMMITTER_NAME": "zouwucode",
    "GIT_COMMITTER_EMAIL": "snapshot@zouwucode.local",
}


class WorkspaceSnapshotter:
    """Side-git snapshot/rollback for one workspace."""

    def __init__(self, workspace, repo_dir, enabled: bool = True, git_timeout: float = 120.0):
        self.workspace = Path(workspace).resolve()
        self.repo_dir = Path(repo_dir).resolve()
        self.enabled = bool(enabled)
        self.git_timeout = float(git_timeout)
        self._ready = False

    # ── capability ──────────────────────────────────────────────────────────
    @property
    def available(self) -> bool:
        return (self.enabled and self.workspace.is_dir()
                and shutil.which("git") is not None)

    # ── internals ───────────────────────────────────────────────────────────
    def _env(self) -> dict:
        env = dict(os.environ)
        env.update({"GIT_DIR": str(self.repo_dir),
                    "GIT_WORK_TREE": str(self.workspace)})
        env.update(_AUTHOR)
        return env

    def _git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=str(self.workspace), env=self._env(),
            capture_output=True, text=True, timeout=self.git_timeout,
        )

    def _ensure_repo(self) -> None:
        if self._ready:
            return
        self.repo_dir.parent.mkdir(parents=True, exist_ok=True)
        self._git("init", "--quiet", "-b", "main")
        # The side repo is deliberately non-bare with an external work tree.
        self._git("config", "core.bare", "false")
        self._git("config", "core.worktree", str(self.workspace))
        info = self.repo_dir / "info"
        info.mkdir(parents=True, exist_ok=True)
        patterns = list(EXCLUDE_PATTERNS)
        # Never snapshot the side repo itself if it lives inside the workspace.
        try:
            rel = self.repo_dir.relative_to(self.workspace)
            patterns.append(f"/{rel.as_posix().rstrip('/')}/")
        except ValueError:
            pass
        (info / "exclude").write_text("\n".join(patterns) + "\n", encoding="utf-8")
        self._ready = True

    # ── public API ──────────────────────────────────────────────────────────
    def snapshot(self, label: str = "") -> "str | None":
        """Commit the current workspace state; return the commit id.

        Returns ``None`` when disabled, when git is unavailable, or when the
        tree is unchanged since the last snapshot (no empty commits).
        """
        if not self.available:
            return None
        try:
            self._ensure_repo()
            self._git("add", "-A")
            if self._git("diff", "--cached", "--quiet").returncode == 0:
                return None  # nothing changed
            r = self._git("commit", "--quiet", "-m", (label or "snapshot")[:200])
            if r.returncode != 0:
                return None
            return self._git("rev-parse", "HEAD").stdout.strip() or None
        except Exception:
            return None

    def count(self) -> int:
        """Number of snapshots taken so far (0 if none / unavailable)."""
        if not self.available:
            return 0
        try:
            self._ensure_repo()
            r = self._git("rev-list", "--count", "HEAD")
            return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else 0
        except Exception:
            return 0

    def list_snapshots(self, limit: int = 20) -> "list[dict]":
        """Newest-first list of ``{commit, at, label}``."""
        if not self.available or self.count() == 0:
            return []
        try:
            r = self._git("log", "--max-count=%d" % int(limit),
                          "--pretty=format:%h%x1f%ad%x1f%s",
                          "--date=format:%Y-%m-%d %H:%M")
            out = []
            for line in r.stdout.splitlines():
                parts = line.split("\x1f")
                if len(parts) == 3:
                    out.append({"commit": parts[0], "at": parts[1], "label": parts[2]})
            return out
        except Exception:
            return []

    def rollback(self, steps: int = 1) -> dict:
        """Restore the workspace to ``HEAD~steps`` (undo the last n turns).

        Uses ``git reset --hard`` so file changes are reverted AND the
        snapshot history is truncated to the target — meaning a second
        rollback moves one turn further back (project snapshots are points
        in the linear turn history, not a stack). Files added after the
        target are removed by the reset.
        """
        if not self.available:
            return {"ok": False,
                    "error": "rollback is disabled or git is unavailable "
                             "(session.rollback_enabled)"}
        try:
            steps = max(1, int(steps))
        except (TypeError, ValueError):
            return {"ok": False, "error": "steps must be an integer"}
        try:
            self._ensure_repo()
            n = self.count()
            # Need n snapshots for HEAD~steps to resolve (n >= steps + 1).
            if n == 0 or steps >= n:
                avail = max(n - 1, 0)
                return {"ok": False,
                        "error": "not enough snapshots: requested %d, available %d"
                                 % (steps, avail),
                        "available_steps": avail}
            target = self._git("rev-parse", "HEAD~%d" % steps).stdout.strip()
            if not target:
                return {"ok": False, "error": "cannot resolve rollback target"}
            changed = [x for x in
                       self._git("diff", "--name-only", target, "HEAD").stdout.split() if x]
            added = [x for x in
                     self._git("diff", "--name-only", "--diff-filter=A",
                               target, "HEAD").stdout.split() if x]
            r = self._git("reset", "--hard", target)
            if r.returncode != 0:
                return {"ok": False, "error": (r.stderr or "reset failed").strip()[:300]}
            removed = [rel for rel in added
                       if not (self.workspace / rel).exists()]
            return {"ok": True, "steps": steps, "target": target[:8],
                    "changed": changed, "removed": removed}
        except Exception as e:  # noqa: BLE001 -- rollback must never crash the UI
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
