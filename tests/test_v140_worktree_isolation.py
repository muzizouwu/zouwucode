"""Regression for v1.4.0: sub-agent worktree isolation.

When enabled, each sub-agent runs in its OWN git worktree on a `dev/sub-<id>`
branch with tools rooted there, so parallel workers never collide. Best-effort:
a non-git workspace (or missing git) falls back to the shared workspace.
"""

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.engine.providers.base import BaseProvider, ModelResponse
from zouwucode.runtime import create_builtin_tools

HAS_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAS_GIT, reason="git not available")


class _FakeProvider(BaseProvider):
    def __init__(self):
        super().__init__({"model": "fake", "api_key": "x"})

    async def chat_stream(self, messages, tools=None, temperature=0.0,
                          max_tokens=65536, stream_thinking=True):
        yield ModelResponse(content="done")

    async def chat(self, messages, tools=None, temperature=0.0, max_tokens=65536):
        return ModelResponse(content="done")


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True, text=True)


def _make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "tester")
    (repo / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _stack(workspace, tmp_path, isolated):
    from zouwucode.engine.loop import EngineLoop
    from zouwucode.tools.registry import ToolRegistry
    from zouwucode.sandbox.permission import PermissionManager
    from zouwucode.agent.coordinator import AgentCoordinator
    from zouwucode.agent.subagent import SubAgentManager

    cfg = ZOUWUCODEConfig()
    cfg.data_dir = str(tmp_path / "data")
    cfg.subagent.worktree_isolation = isolated
    provider = _FakeProvider()
    engine = EngineLoop(cfg, provider)
    sandbox = PermissionManager(cfg.sandbox)
    sandbox.set_workspace(workspace)
    tools = ToolRegistry()
    tools.register_all(create_builtin_tools(sandbox, cfg))
    coord = AgentCoordinator(cfg, engine, tools)
    mgr = SubAgentManager(cfg, provider, coord, workspace_root=workspace)
    return cfg, mgr, coord


class TestWorktreeIsolation:
    def test_default_is_off(self):
        assert ZOUWUCODEConfig().subagent.worktree_isolation is False

    @needs_git
    def test_isolates_into_own_worktree(self, tmp_path):
        repo = _make_repo(tmp_path)
        cfg, mgr, shared = _stack(repo, tmp_path, isolated=True)
        agent = mgr.create("worker", "you are a worker")
        coord = asyncio.run(mgr._maybe_isolate(agent))

        assert coord is not shared                       # a fresh coordinator
        assert agent.worktree is not None
        assert agent.worktree.branch.startswith("dev/sub-")
        assert Path(agent.worktree.path) != repo
        assert (Path(agent.worktree.path) / "a.py").exists()   # checkout present
        # isolated registry cannot spawn further sub-agents (no `task`)
        assert "task" not in coord.tools.get_names()
        # and it is cached — a second call returns the same coordinator
        assert asyncio.run(mgr._maybe_isolate(agent)) is coord

    def test_disabled_uses_shared(self, tmp_path):
        repo = tmp_path / "plain"
        repo.mkdir()
        cfg, mgr, shared = _stack(repo, tmp_path, isolated=False)
        agent = mgr.create("worker", "x")
        assert asyncio.run(mgr._maybe_isolate(agent)) is shared
        assert agent.worktree is None

    def test_non_git_workspace_falls_back(self, tmp_path):
        plain = tmp_path / "no-git"
        plain.mkdir()
        cfg, mgr, shared = _stack(plain, tmp_path, isolated=True)
        agent = mgr.create("worker", "x")
        coord = asyncio.run(mgr._maybe_isolate(agent))   # must not raise
        assert coord is shared and agent.worktree is None

    @needs_git
    def test_summary_reports_worktree(self, tmp_path):
        repo = _make_repo(tmp_path)
        cfg, mgr, _shared = _stack(repo, tmp_path, isolated=True)
        agent = mgr.create("worker", "x")
        asyncio.run(mgr._maybe_isolate(agent))
        s = agent.summary()
        assert s["worktree"] and s["branch"].startswith("dev/sub-")


class TestWiring:
    def test_runtime_accepts_workspace_root(self):
        import inspect
        from zouwucode.runtime import create_subagents_and_extensions
        params = inspect.signature(create_subagents_and_extensions).parameters
        assert "workspace_root" in params
