"""Dev mode — Devin-style autonomous issue → Draft-PR workflow.

Modules:
- github    : GitHub REST client (issue / PR / comments / labels)
- workspace : git worktree isolation with dev/* branch whitelist
- pipeline  : one task end-to-end (implement → verify → PR)
- queue     : SQLite task queue for async hosting
- cli       : `zouwucode dev` subcommand routing
"""

from .github import GitHubClient, GitHubError, parse_issue_url, parse_repo_slug
from .workspace import WorktreeManager, Workspace, WorkspaceError, validate_branch
from .pipeline import DevPipeline, DevResult
from .queue import DevQueue
from .cli import dev_main

__all__ = [
    "GitHubClient", "GitHubError", "parse_issue_url", "parse_repo_slug",
    "WorktreeManager", "Workspace", "WorkspaceError", "validate_branch",
    "DevPipeline", "DevResult",
    "DevQueue",
    "dev_main",
]
