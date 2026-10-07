"""Tests for dev mode — Devin-style autonomous issue → Draft-PR workflow.

Covers: GitHub URL parsing, branch safety whitelist, real git worktrees,
SQLite queue semantics, pipeline end-to-end (stubbed agent + mocked GitHub),
and the engine cost breaker.
"""

import asyncio
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.dev.github import (
    GitHubClient, GitHubError, parse_issue_url, parse_repo_slug,
)
from zouwucode.dev.workspace import (
    WorktreeManager, WorkspaceError, validate_branch,
)
from zouwucode.dev.queue import DevQueue
from zouwucode.dev.pipeline import DevPipeline, DevResult


# ── URL / ref parsing ────────────────────────────────────────────────────────

class TestParsing:
    def test_parse_issue_url(self):
        assert parse_issue_url(
            "https://github.com/foo/bar/issues/42"
        ) == ("foo", "bar", 42)

    def test_parse_pull_url(self):
        assert parse_issue_url(
            "https://github.com/foo/bar/pull/7"
        ) == ("foo", "bar", 7)

    def test_parse_non_url(self):
        assert parse_issue_url("just fix the bug") is None

    def test_parse_repo_slug(self):
        assert parse_repo_slug("foo/bar#12") == ("foo", "bar", 12)
        assert parse_repo_slug("foo/bar") == ("foo", "bar", None)
        assert parse_repo_slug("not a slug") is None


# ── Branch safety whitelist ──────────────────────────────────────────────────

class TestBranchSafety:
    def test_dev_branches_allowed(self):
        assert validate_branch("dev/issue-1", "dev") == "dev/issue-1"
        assert validate_branch("dev/fix-login-2", "dev") == "dev/fix-login-2"

    def test_protected_branches_rejected(self):
        for b in ("main", "master", "develop", "release", "stable"):
            with pytest.raises(WorkspaceError):
                validate_branch(b, "dev")

    def test_foreign_prefix_rejected(self):
        with pytest.raises(WorkspaceError, match="does not start with"):
            validate_branch("feature/x", "dev")

    def test_unsafe_chars_rejected(self):
        with pytest.raises(WorkspaceError):
            validate_branch("dev/a b;rm -rf", "dev")

    def test_empty_rejected(self):
        with pytest.raises(WorkspaceError):
            validate_branch("", "dev")


# ── Real git worktrees ───────────────────────────────────────────────────────

@pytest.fixture()
def git_repo(tmp_path):
    """A real git repo with a bare origin remote and one commit."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   check=True, capture_output=True)
    repo = tmp_path / "repo"
    env = ["-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-b", "main", str(repo)],
                   check=True, capture_output=True)
    (repo / "hello.txt").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", *env, "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", *env, "-C", str(repo), "commit", "-m", "init"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    str(origin)], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-u", "origin", "main"],
                   check=True, capture_output=True)
    return repo


class TestWorktreeManager:
    def test_create_commit_push(self, git_repo):
        mgr = WorktreeManager(git_repo)

        async def scenario():
            ws = await mgr.create("t1", "dev/issue-1")
            assert ws.path.exists()
            assert (ws.path / "hello.txt").exists()
            (ws.path / "new.txt").write_text("world\n", encoding="utf-8")
            sha = await mgr.commit_all(ws, "dev: change")
            assert sha
            await mgr.push_branch(ws)
            # Pushed branch exists in origin
            out = subprocess.run(
                ["git", "--git-dir", str(git_repo / "../origin.git"),
                 "rev-parse", "refs/heads/dev/issue-1"],
                capture_output=True, text=True)
            assert out.returncode == 0
            return ws

        ws = asyncio.run(scenario())

        async def cleanup():
            await mgr.remove(ws)
        asyncio.run(cleanup())
        assert not ws.path.exists()

    def test_rejects_non_dev_branch(self, git_repo):
        mgr = WorktreeManager(git_repo)
        with pytest.raises(WorkspaceError):
            asyncio.run(mgr.create("t1", "main"))

    def test_idempotent_reuse(self, git_repo):
        mgr = WorktreeManager(git_repo)

        async def scenario():
            ws1 = await mgr.create("t1", "dev/issue-1")
            ws2 = await mgr.create("t1", "dev/issue-1")  # re-run
            assert ws1.path == ws2.path
        asyncio.run(scenario())

    def test_no_changes_returns_none_commit(self, git_repo):
        mgr = WorktreeManager(git_repo)

        async def scenario():
            ws = await mgr.create("t1", "dev/issue-1")
            sha = await mgr.commit_all(ws, "empty")
            assert sha is None
        asyncio.run(scenario())


# ── SQLite queue ─────────────────────────────────────────────────────────────

class TestDevQueue:
    def test_submit_claim_finish_cycle(self, tmp_path):
        q = DevQueue(tmp_path / "q.sqlite")
        t1 = q.submit("task A")
        t2 = q.submit("task B")

        claimed = q.claim_next()
        assert claimed["id"] == t1            # FIFO
        q.finish(t1, success=True, result={"pr": "x"})

        claimed2 = q.claim_next()
        assert claimed2["id"] == t2
        q.finish(t2, success=False, error="boom")

        stats = q.stats()
        assert stats["done"] == 1 and stats["failed"] == 1
        assert q.claim_next() is None         # queue drained

    def test_claim_is_atomic(self, tmp_path):
        """Two concurrent claims never get the same task."""
        q = DevQueue(tmp_path / "q.sqlite")
        q.submit("only one")
        a = q.claim_next()
        b = q.claim_next()
        assert a is not None and b is None

    def test_requeue_stale(self, tmp_path):
        q = DevQueue(tmp_path / "q.sqlite")
        q.submit("crashed task")
        task = q.claim_next()
        # Simulate a crash: started long ago, timeout short
        conn = sqlite3.connect(str(q.db_path))
        conn.execute("UPDATE dev_tasks SET started_at=?, timeout=1 WHERE id=?",
                     (time.time() - 100, task["id"]))
        conn.commit()
        conn.close()
        assert q.requeue_stale() == 1
        assert q.claim_next()["id"] == task["id"]  # back in pending

    def test_list_tasks(self, tmp_path):
        q = DevQueue(tmp_path / "q.sqlite")
        q.submit("a")
        q.submit("b")
        tasks = q.list_tasks()
        assert len(tasks) == 2
        assert {t["task_ref"] for t in tasks} == {"a", "b"}


# ── GitHub client ────────────────────────────────────────────────────────────

class TestGitHubClient:
    def test_token_from_env(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_env")
        cfg = ZOUWUCODEConfig()
        client = GitHubClient.from_config(cfg.github)
        assert client.has_token
        assert client._headers()["Authorization"] == "Bearer ghp_env"

    def test_config_token_fallback(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        cfg = ZOUWUCODEConfig()
        cfg.github.token = "ghp_cfg"
        client = GitHubClient.from_config(cfg.github)
        assert client._token == "ghp_cfg"

    def test_no_token(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        client = GitHubClient(token="")
        assert not client.has_token
        assert "Authorization" not in client._headers()

    def test_github_error_carries_status(self):
        err = GitHubError("not found", status_code=404)
        assert err.status_code == 404


# ── Pipeline ─────────────────────────────────────────────────────────────────

class _StubGH:
    """In-memory GitHub API mock for pipeline tests."""

    def __init__(self):
        self.has_token = True
        self.issues = {1: {"title": "Fix login bug", "body": "desc",
                           "html_url": "https://github.com/foo/bar/issues/1"}}
        self.prs_created = []
        self.comments = []
        self.check_runs = []          # list of check-run dicts returned by API

    async def get_issue(self, owner, repo, number):
        return self.issues[number]

    async def create_pr_from_issue(self, owner, repo, *, head, base, title,
                                   body, issue_number, draft=True):
        self.prs_created.append({"head": head, "title": title,
                                 "draft": draft, "body": body})
        return {"html_url": f"https://github.com/{owner}/{repo}/pull/99"}

    async def comment_issue(self, owner, repo, number, body):
        self.comments.append(body)

    async def remove_label(self, *a, **k):
        pass

    async def list_check_runs(self, owner, repo, ref):
        return list(self.check_runs)


def _pipeline_config(tmp_path) -> ZOUWUCODEConfig:
    cfg = ZOUWUCODEConfig()
    cfg.data_dir = str(tmp_path / "data")
    cfg.dev.test_command = ""       # auto-detect → none in tmp repo → skip
    cfg.engine.max_llm_retries = 0
    # Reviewer/CI need network + tokens; covered by dedicated unit tests.
    cfg.dev.review_enabled = False
    cfg.dev.ci_check_enabled = False
    # PLAN phase adds one agent round; covered by its own test below.
    cfg.dev.plan_enabled = False
    return cfg


class TestPipeline:
    def test_resolve_task_free_text(self, git_repo):
        cfg = _pipeline_config(git_repo.parent)
        p = DevPipeline(cfg, git_repo, github=_StubGH())

        async def scenario():
            return await p.resolve_task("重构 utils 模块")
        owner, repo, number, prompt = asyncio.run(scenario())
        assert owner == "" and number == 0
        assert prompt == "重构 utils 模块"

    def test_resolve_task_issue_url(self, git_repo):
        cfg = _pipeline_config(git_repo.parent)
        p = DevPipeline(cfg, git_repo, github=_StubGH())

        async def scenario():
            return await p.resolve_task("https://github.com/foo/bar/issues/1")
        owner, repo, number, prompt = asyncio.run(scenario())
        assert (owner, repo, number) == ("foo", "bar", 1)
        assert "Fix login bug" in prompt

    def test_end_to_end_success(self, git_repo, monkeypatch):
        """issue → worktree → (stubbed agent edits) → commit → push → draft PR."""
        cfg = _pipeline_config(git_repo.parent)
        gh = _StubGH()
        p = DevPipeline(cfg, git_repo, github=gh)

        async def fake_agent(self, engine, prompt, extra_context=""):
            # Simulate the agent making a change inside the worktree.
            Path("fix.txt").write_text("fixed\n", encoding="utf-8")
            return "已修复登录 bug，改动见 fix.txt"

        monkeypatch.setattr(DevPipeline, "_run_agent", fake_agent)

        result = asyncio.run(p.run("https://github.com/foo/bar/issues/1"))
        assert result.success is True
        assert result.branch == "dev/issue-1"
        assert result.pr_url.endswith("/pull/99")
        assert gh.prs_created and gh.prs_created[0]["draft"] is True
        assert "fix.txt" in result.files_changed
        assert result.iterations == 1

    def test_agent_no_changes_fails_without_pr(self, git_repo, monkeypatch):
        cfg = _pipeline_config(git_repo.parent)
        gh = _StubGH()
        p = DevPipeline(cfg, git_repo, github=gh)

        async def fake_agent(self, engine, prompt, extra_context=""):
            return "nothing done"          # no file changes
        monkeypatch.setattr(DevPipeline, "_run_agent", fake_agent)

        result = asyncio.run(p.run("https://github.com/foo/bar/issues/1"))
        assert result.success is False
        assert "no file changes" in result.error
        assert gh.prs_created == []
        assert gh.comments, "失败应回帖到 issue"

    def test_local_task_no_github_link(self, git_repo, monkeypatch):
        cfg = _pipeline_config(git_repo.parent)
        p = DevPipeline(cfg, git_repo, github=_StubGH())

        async def fake_agent(self, engine, prompt, extra_context=""):
            Path("local.txt").write_text("x\n", encoding="utf-8")
            return "done"
        monkeypatch.setattr(DevPipeline, "_run_agent", fake_agent)

        result = asyncio.run(p.run("给 README 加个说明"))
        assert result.success is True
        assert result.pr_url == ""          # 无 issue 关联 → 只建本地分支
        assert result.branch.startswith("dev/")


# ── Engine cost breaker ──────────────────────────────────────────────────────

class TestCostBreaker:
    def test_cost_over_budget_aborts(self, monkeypatch):
        from zouwucode.engine.loop import EngineLoop, TurnLimitExceeded
        from zouwucode.engine.providers.base import BaseProvider, ModelResponse

        class _Provider(BaseProvider):
            async def chat_stream(self, messages, tools=None, temperature=0.0,
                                  max_tokens=65536, stream_thinking=True):
                yield ModelResponse(content="x", tool_calls=[
                    type("TC", (), {"id": "c1", "name": "read",
                                    "arguments": "{}"})()
                ])
                yield ModelResponse(content="", tool_calls=[], usage={})

            async def chat(self, messages, tools=None, temperature=0.0,
                           max_tokens=65536):
                return ModelResponse(content="x")

        cfg = ZOUWUCODEConfig()
        cfg.engine.max_cost_usd = 0.5
        cfg.engine.max_consecutive_tool_errors = 999  # 隔离：只测成本
        engine = EngineLoop(cfg, _Provider({"api_key": "sk-test"}))

        async def executor(tc, ctx):
            return ToolResult_stub(tc.id)
        engine.set_tool_executor(executor)

        # 每轮记账时注入 $1.0 成本（超过 0.5 预算）
        orig = engine.stats.record_turn
        def expensive(*a, **k):
            orig(*a, **k)
            engine.stats.total_cost += 1.0
        monkeypatch.setattr(engine.stats, "record_turn", expensive)

        with pytest.raises(TurnLimitExceeded, match="cost"):
            asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))


class ToolResult_stub:
    def __init__(self, tool_call_id):
        self.tool_call_id = tool_call_id
        self.content = "ok"
        self.is_error = False

    def to_dict(self):
        return {"role": "tool", "tool_call_id": self.tool_call_id,
                "content": self.content}


# ── Multi-layer verification (pure detectors + report) ───────────────────────

from zouwucode.dev import verifiers
from zouwucode.dev import reviewer


class TestVerifierDetectors:
    def test_lint_detects_ruff_on_pyproject(self, tmp_path, monkeypatch):
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
        monkeypatch.setattr(verifiers, "_tool_available", lambda n: n == "ruff")
        assert verifiers.detect_lint_command(tmp_path) == "ruff check ."

    def test_lint_explicit_config_wins(self, tmp_path):
        assert verifiers.detect_lint_command(tmp_path, "eslint .") == "eslint ."

    def test_lint_absent_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(verifiers, "_tool_available", lambda n: False)
        assert verifiers.detect_lint_command(tmp_path) is None

    def test_typecheck_mypy_marker(self, tmp_path, monkeypatch):
        (tmp_path / "pyproject.toml").write_text("[tool.mypy]\n", encoding="utf-8")
        monkeypatch.setattr(verifiers, "_tool_available", lambda n: n == "mypy")
        assert verifiers.detect_typecheck_command(tmp_path) == "mypy ."

    def test_security_requires_bandit_marker(self, tmp_path, monkeypatch):
        monkeypatch.setattr(verifiers, "_tool_available", lambda n: True)
        # No [tool.bandit] → skip (bandit is noisy unless opted in).
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
        assert verifiers.detect_security_command(tmp_path) is None
        (tmp_path / "pyproject.toml").write_text("[tool.bandit]\n", encoding="utf-8")
        assert verifiers.detect_security_command(tmp_path).startswith("bandit")

    def test_coverage_floor_appended_to_pytest(self, tmp_path):
        (tmp_path / "ok.txt").write_text("x", encoding="utf-8")
        report = asyncio.run(verifiers.run_verification(
            tmp_path, lint_command="python -c pass",
            typecheck_command="python -c pass",
            test_command="python -m pytest -q", coverage_min=80))
        test_layer = next(r for r in report.results if r.name == "test")
        assert "--cov-fail-under=80" in test_layer.command


class TestVerificationReport:
    def test_skipped_layers_do_not_block(self):
        rep = verifiers.VerificationReport(results=[
            verifiers.LayerResult(name="lint", skipped=True),
            verifiers.LayerResult(name="test", passed=True),
        ])
        assert rep.passed is True

    def test_failed_layer_blocks(self):
        rep = verifiers.VerificationReport(results=[
            verifiers.LayerResult(name="test", passed=False, output="boom"),
        ])
        assert rep.passed is False
        assert "test" in rep.render_feedback() and "boom" in rep.render_feedback()


# ── Independent reviewer (parse + formatting) ────────────────────────────────

class TestReviewerParsing:
    def test_approve_verdict(self):
        text = ('Nice work.\n```json\n{"verdict":"approve","issues":[],'
                '"summary":"lgtm"}\n```')
        out = reviewer.parse_review(text)
        assert out.approved is True
        assert out.parse_failed is False
        assert out.summary == "lgtm"

    def test_request_changes_with_major(self):
        text = ('```json\n{"verdict":"request_changes","issues":['
                '{"severity":"major","file":"a.py","point":"no error path",'
                '"fix_hint":"wrap in try"}],"summary":"x"}\n```')
        out = reviewer.parse_review(text)
        assert out.approved is False
        assert len(out.issues) == 1

    def test_minor_only_does_not_block(self):
        text = ('```json\n{"verdict":"request_changes","issues":['
                '{"severity":"minor","file":"a.py","point":"style"}],'
                '"summary":"x"}\n```')
        out = reviewer.parse_review(text)
        assert out.approved is True   # no critical/major → not blocking

    def test_unparseable_approves_conservatively(self):
        out = reviewer.parse_review("I think it is fine, no json here")
        assert out.approved is True
        assert out.parse_failed is True

    def test_feedback_lists_only_blocking(self):
        out = reviewer.parse_review(
            '```json\n{"verdict":"request_changes","issues":['
            '{"severity":"critical","file":"a.py","point":"bug",'
            '"fix_hint":"do x"},'
            '{"severity":"minor","file":"b.py","point":"nit"}],'
            '"summary":"s"}\n```')
        fb = reviewer.format_review_feedback(out)
        assert "bug" in fb and "do x" in fb
        assert "nit" not in fb        # minor issues are not fed back as blocking


# ── Pipeline integration: reviewer + CI linkage + adaptive budget ────────────

def _gated_config(tmp_path) -> ZOUWUCODEConfig:
    cfg = _pipeline_config(tmp_path)
    cfg.dev.review_enabled = True
    cfg.dev.ci_check_enabled = True
    cfg.dev.ci_wait_seconds = 1.0
    cfg.dev.ci_poll_interval = 1.0
    return cfg


class TestPipelineQualityGates:
    def test_reviewer_request_changes_then_fix(self, git_repo, monkeypatch):
        """Reviewer blocks once, implementer fixes, second review approves."""
        cfg = _gated_config(git_repo.parent)
        gh = _StubGH()
        p = DevPipeline(cfg, git_repo, github=gh)

        async def fake_agent(self, engine, prompt, extra_context=""):
            Path("fix.txt").write_text("fixed\n", encoding="utf-8")
            return "done"
        monkeypatch.setattr(DevPipeline, "_run_agent", fake_agent)

        verdicts = [
            reviewer.ReviewOutcome(approved=False,
                                   issues=[{"severity": "major", "file": "fix.txt",
                                            "point": "missing guard"}]),
            reviewer.ReviewOutcome(approved=True, summary="clean"),
        ]

        async def fake_review(engine, prompt, diff, tool_schemas=None):
            return verdicts.pop(0)
        monkeypatch.setattr(reviewer, "review_diff", fake_review)

        result = asyncio.run(p.run("https://github.com/foo/bar/issues/1"))
        assert result.success is True
        assert verdicts == []                       # both review rounds consumed
        assert "独立 AI 审查" in result.review_summary

    def test_ci_failing_reported_but_pr_still_draft(self, git_repo, monkeypatch):
        """Real CI disagreement is surfaced, not silently swallowed."""
        cfg = _gated_config(git_repo.parent)
        cfg.dev.ci_wait_seconds = 1.0
        cfg.dev.ci_poll_interval = 1.0
        gh = _StubGH()
        gh.check_runs = [{"name": "CI", "status": "completed",
                          "conclusion": "failure"}]
        p = DevPipeline(cfg, git_repo, github=gh)

        async def fake_agent(self, engine, prompt, extra_context=""):
            Path("fix.txt").write_text("fixed\n", encoding="utf-8")
            return "done"
        monkeypatch.setattr(DevPipeline, "_run_agent", fake_agent)

        async def fake_review(engine, prompt, diff, tool_schemas=None):
            return reviewer.ReviewOutcome(approved=True)
        monkeypatch.setattr(reviewer, "review_diff", fake_review)

        result = asyncio.run(p.run("https://github.com/foo/bar/issues/1"))
        assert result.ci_status == "failing"
        assert result.success is True               # non-blocking by design
        assert gh.prs_created[0]["draft"] is True   # still Draft
        assert any("CI" in c for c in gh.comments)  # reported to the PR/issue

    def test_adaptive_budget_escalates_on_cost(self, git_repo, monkeypatch):
        """A cost abort doubles the budget and resumes, instead of failing."""
        cfg = _pipeline_config(git_repo.parent)
        cfg.dev.adaptive_budget = True
        cfg.dev.budget_escalations = 1
        cfg.dev.task_cost_budget_usd = 1.0
        p = DevPipeline(cfg, git_repo, github=_StubGH())

        calls = {"n": 0}

        async def fake_agent(self, engine, prompt, extra_context=""):
            calls["n"] += 1
            if calls["n"] == 1:
                from zouwucode.engine.loop import TurnLimitExceeded
                raise TurnLimitExceeded("Task cost $1.2 exceeded budget $1.00")
            Path("fix.txt").write_text("ok\n", encoding="utf-8")
            return "done after escalation"
        monkeypatch.setattr(DevPipeline, "_run_agent", fake_agent)

        result = asyncio.run(p.run("some local task"))
        assert calls["n"] == 2                       # aborted, escalated, resumed
        assert result.success is True

    def test_plan_phase_runs_first(self, git_repo, monkeypatch):
        """plan_enabled=True → the agent gets a planning round before coding."""
        cfg = _pipeline_config(git_repo.parent)
        cfg.dev.plan_enabled = True
        p = DevPipeline(cfg, git_repo, github=_StubGH())

        seen = []

        async def fake_agent(self, engine, prompt, extra_context=""):
            seen.append(extra_context)
            if len(seen) == 1:
                return "1. fix calc.py\n2. run tests"      # plan output
            Path("fix.txt").write_text("ok\n", encoding="utf-8")
            return "done"
        monkeypatch.setattr(DevPipeline, "_run_agent", fake_agent)

        result = asyncio.run(p.run("some local task"))
        assert result.success is True
        assert len(seen) == 2
        assert "planning" in seen[0].lower()   # plan prompt appended
        assert seen[1] == ""                   # implement round has no extra

    def test_adaptive_budget_disabled_propagates(self, git_repo, monkeypatch):
        """With adaptive_budget off, a cost abort fails the task as before."""
        cfg = _pipeline_config(git_repo.parent)
        cfg.dev.adaptive_budget = False
        cfg.dev.plan_enabled = False
        p = DevPipeline(cfg, git_repo, github=_StubGH())

        async def fake_agent(self, engine, prompt, extra_context=""):
            from zouwucode.engine.loop import TurnLimitExceeded
            raise TurnLimitExceeded("Task cost exceeded budget")
        monkeypatch.setattr(DevPipeline, "_run_agent", fake_agent)

        result = asyncio.run(p.run("some local task"))
        assert result.success is False
        assert "cost" in result.error.lower()
