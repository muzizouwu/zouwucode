"""GitHub API client — the issue → PR backbone of dev mode.

Thin async wrapper over the GitHub REST API using httpx (already a core
dependency). Only the endpoints the dev workflow needs are implemented:
read an issue, open a draft PR, comment, and query/label issues for watch
mode. Auth token comes from GITHUB_TOKEN env var (preferred) or
config.github.token.

Design note: every method takes owner/repo explicitly so a single client
can serve multiple repositories (watch mode across repos).
"""

import os
import re
from typing import Optional
from urllib.parse import quote

import httpx


class GitHubError(RuntimeError):
    """GitHub API call failed (non-2xx or transport error)."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


# Matches https://github.com/<owner>/<repo>/issues/<n> and .../pull/<n>
_ISSUE_URL_RE = re.compile(
    r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?:issues|pull)/(?P<number>\d+)"
)


def parse_issue_url(url: str) -> Optional[tuple[str, str, int]]:
    """Extract (owner, repo, issue_number) from a GitHub issue/PR URL.

    Returns None if the string is not such a URL (callers may then treat
    the input as a bare "owner/repo#N" reference or plain task text).
    """
    m = _ISSUE_URL_RE.search(url)
    if not m:
        return None
    return m.group("owner"), m.group("repo"), int(m.group("number"))


def parse_repo_slug(text: str) -> Optional[tuple[str, str]]:
    """Parse "owner/repo" (optionally "#N") into (owner, repo, number|None)."""
    m = re.match(r"^(?P<owner>[^/#\s]+)/(?P<repo>[^#\s]+)(?:#(?P<number>\d+))?$",
                 text.strip())
    if not m:
        return None
    num = int(m.group("number")) if m.group("number") else None
    return m.group("owner"), m.group("repo"), num


class GitHubClient:
    """Minimal async GitHub REST client for the dev workflow."""

    def __init__(self, token: str, api_base: str = "https://api.github.com"):
        self._token = token or ""
        self._api_base = api_base.rstrip("/")

    @classmethod
    def from_config(cls, github_cfg) -> "GitHubClient":
        # Env var wins so CI / users can inject without editing config.
        token = os.environ.get("GITHUB_TOKEN", "") or getattr(github_cfg, "token", "")
        return cls(token=token, api_base=getattr(github_cfg, "api_base",
                                                 "https://api.github.com"))

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    def _headers(self) -> dict:
        h = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "zouwucode-dev",
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def _request(self, method: str, path: str, *,
                       json_body: Optional[dict] = None,
                       params: Optional[dict] = None) -> dict:
        url = f"{self._api_base}{path}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.request(method, url, headers=self._headers(),
                                             json=json_body, params=params)
            except httpx.TransportError as exc:
                raise GitHubError(f"GitHub transport error: {exc}") from exc
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.json().get("message", "")
            except Exception:
                detail = resp.text[:200]
            raise GitHubError(
                f"GitHub {method} {path} → {resp.status_code}: {detail}",
                status_code=resp.status_code,
            )
        # 204 No Content and empty bodies
        if not resp.content:
            return {}
        return resp.json()

    # ── Read ──────────────────────────────────────────────────────────────

    async def get_issue(self, owner: str, repo: str, number: int) -> dict:
        return await self._request(
            "GET", f"/repos/{owner}/{repo}/issues/{number}"
        )

    async def list_issues_by_label(self, owner: str, repo: str, label: str,
                                   limit: int = 20) -> list[dict]:
        data = await self._request(
            "GET", f"/repos/{owner}/{repo}/issues",
            params={"labels": label, "state": "open", "per_page": limit},
        )
        # The issues endpoint also returns PRs; filter them out.
        return [it for it in data if "pull_request" not in it]

    # ── Write ─────────────────────────────────────────────────────────────

    async def comment_issue(self, owner: str, repo: str, number: int,
                            body: str) -> dict:
        return await self._request(
            "POST", f"/repos/{owner}/{repo}/issues/{number}/comments",
            json_body={"body": body},
        )

    async def add_labels(self, owner: str, repo: str, number: int,
                        labels: list[str]) -> dict:
        return await self._request(
            "POST", f"/repos/{owner}/{repo}/issues/{number}/labels",
            json_body={"labels": labels},
        )

    async def remove_label(self, owner: str, repo: str, number: int,
                           label: str) -> None:
        await self._request(
            "DELETE",
            f"/repos/{owner}/{repo}/issues/{number}/labels/{quote(label, safe='')}",
        )

    async def create_pull(self, owner: str, repo: str, *, head: str,
                          base: str, title: str, body: str,
                          draft: bool = True) -> dict:
        return await self._request(
            "POST", f"/repos/{owner}/{repo}/pulls",
            json_body={
                "head": head, "base": base, "title": title, "body": body,
                "draft": draft,
            },
        )

    async def create_pr_from_issue(self, owner: str, repo: str,
                                   head: str, base: str, title: str,
                                   body: str, issue_number: int,
                                   draft: bool = True) -> dict:
        """Open a PR that auto-closes issue #N (GitHub 'Closes #N' keyword)."""
        full_body = f"{body}\n\nCloses #{issue_number}"
        return await self.create_pull(owner=owner, repo=repo, head=head,
                                      base=base, title=title, body=full_body,
                                      draft=draft)
