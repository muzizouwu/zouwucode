"""DevPipeline — the Devin-style autonomous issue → Draft-PR workflow.

One pipeline run = one task, executed end-to-end:

    issue/task text
      → isolated git worktree (dev/* branch)
      → autonomous agent session (EngineLoop, yolo mode, cost-capped)
      → verification loop: run tests; on failure feed output back into the
        SAME conversation so the agent sees and fixes it (bounded retries)
      → success: commit + push dev/* + **Draft PR** (never auto-merged)
        failure: no PR; report posted as issue comment (or console in
        local mode)

The agent session runs with the worktree as CWD so every file/shell tool
operates inside the isolated checkout. Long-task safety limits (rounds,
timeouts, consecutive-error breaker, cost budget) all stay active.
"""

import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import ZOUWUCODEConfig
from ..engine.loop import EngineLoop, TaskInterrupted, TurnLimitExceeded
from ..runtime import create_provider, create_builtin_tools
from ..tools.registry import ToolRegistry
from ..sandbox.permission import PermissionManager
from ..agent.coordinator import AgentCoordinator
from ..agent.subagent import SubAgentManager
from ..project_memory import ProjectMemory
from .github import GitHubClient, GitHubError
from .workspace import WorktreeManager, WorkspaceError, validate_branch

logger = logging.getLogger("zouwucode.dev.pipeline")

# Prompt for the autonomous worker session.
_SYSTEM_PROMPT = """You are ZOUWUCODE Dev, an autonomous software engineer \
working inside an isolated git worktree. Complete the assigned task end-to-end:
understand the code, implement the change, and verify it with the project's \
tests. Rules:
1. Stay within this worktree directory.
2. Never create commits yourself — verification and commit are handled by \
the harness after you finish.
3. Prefer minimal, focused changes that satisfy the task.
4. When done, summarize: what changed, why, and how it was verified.
"""


@dataclass
class DevResult:
    """Outcome of one pipeline run."""

    success: bool
    branch: str = ""
    pr_url: str = ""
    summary: str = ""            # agent's final report
    verification_output: str = ""
    error: str = ""
    cost_usd: float = 0.0
    iterations: int = 0          # verification rounds used
    files_changed: list[str] = field(default_factory=list)


class DevPipeline:
    """Runs one dev task to completion (or documented failure)."""

    def __init__(
        self,
        config: ZOUWUCODEConfig,
        repo_root: Path,
        github: Optional[GitHubClient] = None,
        *,
        owner: str = "",
        repo: str = "",
    ):
        self.config = config
        self.repo_root = Path(repo_root).resolve()
        self.dev_cfg = config.dev
        self.github = github or GitHubClient.from_config(config.github)
        self.owner = owner
        self.repo = repo
        self.worktrees = WorktreeManager(
            self.repo_root, self.dev_cfg.worktree_dir, self.dev_cfg.branch_prefix,
        )

    # ── Task input parsing ─────────────────────────────────────────────────

    async def resolve_task(self, task_ref: str) -> tuple[str, str, int, str]:
        """Turn a task reference into (owner, repo, issue_number|0, prompt).

        Accepted forms:
          - https://github.com/o/r/issues/N  → fetch issue title+body
          - o/r#N                             → same via API
          - free text                         → local task, no GitHub linkage
        """
        from .github import parse_issue_url, parse_repo_slug

        ref = task_ref.strip()
        parsed = parse_issue_url(ref) or None
        number = 0
        if parsed is None:
            slug = parse_repo_slug(ref)
            if slug and slug[2]:
                parsed = (slug[0], slug[1], slug[2])

        if parsed:
            owner, repo, number = parsed
            if not self.github.has_token:
                raise GitHubError(
                    f"Task references GitHub issue {owner}/{repo}#{number}, "
                    "but no token is configured. Set GITHUB_TOKEN or "
                    "config.github.token to fetch the issue."
                )
            issue = await self.github.get_issue(owner, repo, number)
            prompt = (
                f"GitHub issue #{number}: {issue.get('title', '')}\n\n"
                f"{issue.get('body') or '(no description)'}"
            )
            return owner, repo, number, prompt

        # Free-text local task.
        return self.owner, self.repo, 0, ref

    # ── Agent session ──────────────────────────────────────────────────────

    def _build_engine(self) -> EngineLoop:
        """Fresh engine per task: isolated cache, yolo mode, full safety net.

        Cost budget comes from config.engine.max_cost_usd (engine-enforced).
        """
        cfg = self.config
        provider = create_provider(cfg)
        engine = EngineLoop(cfg, provider)
        engine.set_mode("yolo")
        sandbox = PermissionManager(cfg.sandbox)
        sandbox.set_workspace(Path.cwd())
        tools = ToolRegistry()
        tools.register_all(create_builtin_tools(sandbox))
        coordinator = AgentCoordinator(cfg, engine, tools)
        engine.set_tool_executor(coordinator.execute_tool)
        # Sub-agents available to the dev worker too (parallel research).
        manager = SubAgentManager(cfg, provider, coordinator)
        manager.bind_main_engine(engine)
        from ..tools.agent_tools import TaskTool
        tools.register(TaskTool(manager))
        self._current_tools = tools
        return engine

    async def _run_agent(self, engine: EngineLoop, prompt: str,
                         extra_context: str = "") -> str:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt + (extra_context or "")},
        ]
        response = await engine.run(
            messages=messages, tools=self._current_tools.get_schemas()
        )
        return response.content or ""

    # ── Verification ───────────────────────────────────────────────────────

    def _detect_test_command(self) -> Optional[str]:
        """Auto-detect a sensible test command for the worktree."""
        cwd = Path.cwd()
        if (cwd / "pytest.ini").exists() or (cwd / "tests").is_dir() \
                or (cwd / "pyproject.toml").exists():
            return "python -m pytest -q"
        if (cwd / "package.json").exists():
            try:
                pkg = json.loads((cwd / "package.json").read_text(encoding="utf-8"))
                if "test" in (pkg.get("scripts") or {}):
                    return "npm test"
            except Exception:
                pass
        return None

    async def _run_tests(self) -> tuple[bool, str]:
        cmd = self.dev_cfg.test_command or self._detect_test_command()
        if not cmd:
            return True, "(no test command detected — verification skipped)"
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
        except asyncio.TimeoutError:
            proc.kill()
            return False, f"test command timed out: {cmd}"
        output = stdout.decode("utf-8", errors="replace")
        return proc.returncode == 0, f"$ {cmd}\n{output[-4000:]}"

    # ── Main entry ─────────────────────────────────────────────────────────

    async def run(self, task_ref: str, *, post_report: bool = True) -> DevResult:
        """Execute the full dev workflow for one task reference."""
        owner, repo, number, prompt = await self.resolve_task(task_ref)
        self.owner, self.repo = owner, repo
        task_id = f"issue-{number}" if number else _slug(task_ref)
        branch = f"{self.dev_cfg.branch_prefix}/{task_id}"
        validate_branch(branch, self.dev_cfg.branch_prefix)

        result = DevResult(success=False, branch=branch)

        old_cwd = Path.cwd()
        try:
            ws = await self.worktrees.create(task_id, branch)
        except (WorkspaceError, GitHubError) as exc:
            result.error = f"workspace: {exc}"
            return result

        try:
            os.chdir(ws.path)
            # Build the engine AFTER chdir so its sandbox workspace root is
            # the worktree itself — the agent is confined to this checkout
            # and cannot touch files outside it (path whitelist enforcement).
            engine = self._build_engine()
            logger.info("Dev task %s started in worktree %s", task_id, ws.path)

            # Round 0: autonomous implementation
            summary = await self._run_agent(engine, prompt)
            result.summary = summary

            # Verification loop (stage 3): tests → feedback → fix → retest
            for iteration in range(self.dev_cfg.verify_retries + 1):
                ok, output = await self._run_tests()
                result.verification_output = output
                result.iterations = iteration + 1
                if ok:
                    break
                if iteration >= self.dev_cfg.verify_retries:
                    result.error = f"tests failed after {iteration + 1} attempt(s)"
                    await self._report_failure(number, output, prompt, post_report)
                    return result
                # Feed the failure back into the SAME conversation context.
                logger.info("Verification failed — iteration %d, feeding back",
                            iteration + 1)
                feedback = (
                    "\n\n## Verification result (FAILED)\n"
                    "The project tests did not pass after your changes:\n\n"
                    f"```\n{output}\n```\n\n"
                    "Fix the remaining issues and re-run to completion."
                )
                summary = await self._run_agent(engine, prompt, extra_context=feedback)
                result.summary = summary
            else:
                # loop exhausted without break → last test run failed
                return result

            # Success path: commit → push → draft PR / local report
            committed = await self.worktrees.commit_all(
                ws, f"dev: {prompt.splitlines()[0][:72]} [ZOUWUCODE]")
            if committed:
                await self.worktrees.push_branch(ws)
                diffstat = await self.worktrees.diff_summary(ws, ws.base_ref)
                result.files_changed = _parse_diffstat_files(diffstat)
                if owner and repo and number:
                    pr = await self.github.create_pr_from_issue(
                        owner, repo, head=branch, base=ws.base_ref,
                        title=f"dev: {prompt.splitlines()[0][:72]}",
                        body=_pr_body(prompt, result, diffstat),
                        issue_number=number,
                        draft=self.dev_cfg.draft_pr,
                    )
                    result.pr_url = pr.get("html_url", "")
                result.success = True
                self._harvest_learning(task_id, result)
                if post_report and not (owner and repo and number):
                    logger.info("Local dev task done (no GitHub link): %s",
                                result.pr_url or branch)
            else:
                result.error = "agent finished but produced no file changes"
                await self._report_failure(number, "no changes produced",
                                           prompt, post_report)
            return result

        except (TurnLimitExceeded, TaskInterrupted) as exc:
            result.error = f"aborted: {exc}"
            await self._report_failure(number, str(exc), prompt, post_report)
            return result
        except (WorkspaceError, GitHubError) as exc:
            result.error = str(exc)
            await self._report_failure(number, str(exc), prompt, post_report)
            return result
        finally:
            os.chdir(old_cwd)
            result.cost_usd = engine.stats.total_cost

    async def _report_failure(self, number: int, detail: str,
                              prompt: str, post_report: bool) -> None:
        """Post failure as issue comment (GitHub mode) or log locally."""
        if number and self.owner and self.repo and self.github.has_token:
            try:
                await self.github.comment_issue(
                    self.owner, self.repo, number,
                    f"🤖 **ZOUWUCODE dev** 未能自动完成此任务。\n\n"
                    f"任务：{prompt.splitlines()[0][:200]}\n\n"
                    f"```\n{detail[-2000:]}\n```\n\n"
                    "请人工介入，或将任务拆分得更明确后重新打 `zouwucode:do` 标签。",
                )
            except GitHubError as exc:
                logger.warning("Failed to post issue comment: %s", exc)
        if post_report:
            logger.error("Dev task failed: %s", detail[:500])

    def _harvest_learning(self, task_id: str, result: DevResult) -> None:
        """Stage 3: persist successful-task experience to project memory."""
        try:
            pm = ProjectMemory()
            pm.load_all()
            pm.add_decision(
                f"dev/{task_id}",
                f"自主完成（{result.iterations} 轮验证，"
                f"改动 {len(result.files_changed)} 个文件）："
                f"{result.summary[:300]}",
            )
            pm.save_all()
        except Exception as exc:  # noqa: BLE001 — memory is best-effort
            logger.warning("Could not harvest dev learning: %s", exc)


# ── helpers ─────────────────────────────────────────────────────────────────

def _slug(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", text.lower())
    return "-".join(words[:6]) or f"task-{abs(hash(text)) % 10000}"


def _parse_diffstat_files(diffstat: str) -> list[str]:
    files = []
    for line in diffstat.splitlines():
        m = re.match(r"\s*(\S+)\s+\|", line)
        if m:
            files.append(m.group(1))
    return files


def _pr_body(prompt: str, result: DevResult, diffstat: str) -> str:
    return (
        f"## 🤖 ZOUWUCODE dev — 自主完成\n\n"
        f"**任务**\n> {prompt.splitlines()[0][:500]}\n\n"
        f"**实现摘要**\n{result.summary[:3000]}\n\n"
        f"**验证**（{result.iterations} 轮）\n```\n"
        f"{result.verification_output[-1500:]}\n```\n\n"
        f"**变更文件**\n```\n{diffstat[:2000]}\n```\n\n"
        f"**成本** ${result.cost_usd:.4f}\n\n"
        f"⚠️ 本 PR 由 AI 自主生成，处于 Draft 状态，请人工 review 后合并。"
    )
