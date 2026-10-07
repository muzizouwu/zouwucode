"""Isolated dev workspaces via git worktree — the Devin-style sandbox.

Each dev task runs in its own `git worktree` under a dedicated branch, so:
- the user's main checkout is never touched (no stashing, no dirty state);
- parallel tasks don't collide (separate dirs + separate branches);
- cleanup is a single `worktree remove`.

SAFETY: every branch operation is validated against the configured prefix
(default: dev/*). Force-push and operations on non-dev branches are refused
at this layer, independent of whatever the LLM decides to request.
"""

import asyncio
import logging
import re
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger("zouwucode.dev.workspace")

# Branch names we refuse to touch even if prefixed (defense in depth).
_FORBIDDEN_BRANCHES = {"main", "master", "develop", "release", "stable"}

# Identity used for automated dev commits. Injected via `git -c` so the
# pipeline never depends on a global/user git config being present (CI
# runners have none, and `git commit` fails with "Author identity unknown").
_DEV_AUTHOR_NAME = "ZOUWUCODE Dev"
_DEV_AUTHOR_EMAIL = "dev@zouwucode.local"


class WorkspaceError(RuntimeError):
    """A workspace/branch operation was refused or failed."""


def validate_branch(branch: str, prefix: str) -> str:
    """Return *branch* if it is a safe dev branch; raise otherwise.

    Rules: must start with "<prefix>/" and must not be a protected name.
    """
    if not branch or not branch.strip():
        raise WorkspaceError("Branch name is empty.")
    branch = branch.strip()
    if branch in _FORBIDDEN_BRANCHES:
        raise WorkspaceError(f"Refusing to operate on protected branch: {branch}")
    if not branch.startswith(f"{prefix}/"):
        raise WorkspaceError(
            f"Branch '{branch}' does not start with '{prefix}/' — dev mode "
            f"only operates on '{prefix}/*' branches."
        )
    # Reject characters that could break shell/quoting assumptions.
    if not re.fullmatch(r"[\w./@-]+", branch):
        raise WorkspaceError(f"Branch '{branch}' contains unsafe characters.")
    return branch


class Workspace:
    """A checked-out worktree for one dev task."""

    def __init__(self, path: Path, branch: str, base_ref: str):
        self.path = path
        self.branch = branch
        self.base_ref = base_ref  # the ref the branch was created from

    def __repr__(self) -> str:
        return f"<Workspace {self.branch} @ {self.path}>"


class WorktreeManager:
    """Creates/inspects/removes git worktrees for dev tasks."""

    def __init__(self, repo_root: Path, worktree_dir: str = ".zouwucode_worktrees",
                 branch_prefix: str = "dev"):
        self.repo_root = Path(repo_root).resolve()
        self.worktree_dir = self.repo_root / worktree_dir
        self.branch_prefix = branch_prefix

    # ── Low-level git runner ───────────────────────────────────────────────

    async def _git(self, *args: str, cwd: Optional[Path] = None) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(cwd or self.repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            raise WorkspaceError(f"git {' '.join(args)} failed: {err.strip()[:300]}")
        return out

    # ── Public API ─────────────────────────────────────────────────────────

    async def ensure_repo(self) -> None:
        if not (self.repo_root / ".git").exists():
            raise WorkspaceError(f"Not a git repository: {self.repo_root}")

    async def current_base_ref(self) -> str:
        """HEAD ref name the dev branch should be based on."""
        try:
            return (await self._git("rev-parse", "--abbrev-ref", "HEAD")).strip()
        except WorkspaceError:
            return "HEAD"

    def _safe_task_dir(self, task_id: str) -> Path:
        # task_id is used as a directory name — sanitize it.
        safe = re.sub(r"[^\w-]", "-", str(task_id))[:60] or "task"
        return self.worktree_dir / safe

    async def create(self, task_id: str, branch: str,
                     base_ref: Optional[str] = None) -> Workspace:
        """Create a worktree checked out to a new dev branch.

        If the branch already exists (re-run of the same task), it is
        reused rather than recreated — keeps resume semantics.
        """
        await self.ensure_repo()
        validate_branch(branch, self.branch_prefix)
        base = base_ref or await self.current_base_ref()
        wt_path = self._safe_task_dir(task_id)
        self.worktree_dir.mkdir(parents=True, exist_ok=True)

        if wt_path.exists():
            # Reuse existing worktree (idempotent re-run).
            logger.info("Reusing existing worktree %s", wt_path)
            return Workspace(wt_path, branch, base)

        try:
            await self._git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
            # Branch exists → worktree with existing branch
            await self._git("worktree", "add", str(wt_path), branch)
        except WorkspaceError:
            # Branch doesn't exist → create from base
            await self._git("worktree", "add", "-b", branch, str(wt_path), base)

        logger.info("Created worktree %s (branch=%s base=%s)", wt_path, branch, base)
        return Workspace(wt_path, branch, base)

    async def commit_all(self, ws: Workspace, message: str) -> Optional[str]:
        """Stage + commit everything in the worktree. Returns commit sha or
        None when there was nothing to commit."""
        await self._git("add", "-A", cwd=ws.path)
        status = await self._git("status", "--porcelain", cwd=ws.path)
        if not status.strip():
            return None
        await self._git(
            "-c", f"user.name={_DEV_AUTHOR_NAME}",
            "-c", f"user.email={_DEV_AUTHOR_EMAIL}",
            "commit", "-m", message, cwd=ws.path,
        )
        return (await self._git("rev-parse", "HEAD", cwd=ws.path)).strip()

    async def push_branch(self, ws: Workspace, remote: str = "origin",
                          force: bool = False) -> None:
        """Push the dev branch. force is only allowed for dev/* branches
        (validated again here) — never for protected branches."""
        validate_branch(ws.branch, self.branch_prefix)
        args = ["push", remote, f"HEAD:refs/heads/{ws.branch}"]
        if force:
            args.insert(1, "--force")  # safe: branch validated above
        await self._git(*args, cwd=ws.path)
        logger.info("Pushed branch %s to %s", ws.branch, remote)

    async def branch_has_commits(self, ws: Workspace, base_ref: str) -> bool:
        """True if the worktree branch diverged from base (has new commits)."""
        try:
            out = await self._git(
                "rev-list", "--count", f"{base_ref}..HEAD", cwd=ws.path
            )
            return int(out.strip()) > 0
        except WorkspaceError:
            return True  # base unknown → assume diverged, let PR creation decide

    async def remove(self, ws: Workspace, delete_branch: bool = False) -> None:
        """Remove the worktree (and optionally its branch)."""
        try:
            await self._git("worktree", "remove", "--force", str(ws.path))
        except WorkspaceError as exc:
            logger.warning("worktree remove failed (%s); falling back to rmtree", exc)
            shutil.rmtree(ws.path, ignore_errors=True)
            try:
                await self._git("worktree", "prune")
            except WorkspaceError:
                pass
        if delete_branch:
            validate_branch(ws.branch, self.branch_prefix)
            try:
                await self._git("branch", "-D", ws.branch)
            except WorkspaceError:
                pass

    async def diff_summary(self, ws: Workspace, base_ref: str) -> str:
        """Short diffstat vs base for the PR body."""
        try:
            return await self._git("diff", "--stat", f"{base_ref}...HEAD",
                                   cwd=ws.path)
        except WorkspaceError:
            return "(diff unavailable)"

    async def diff_text(self, ws: Workspace, base_ref: str,
                        max_chars: int = 60_000) -> str:
        """Full unified diff vs base — the input for independent review."""
        try:
            out = await self._git("diff", f"{base_ref}...HEAD", cwd=ws.path)
        except WorkspaceError:
            return ""
        return out[:max_chars]
