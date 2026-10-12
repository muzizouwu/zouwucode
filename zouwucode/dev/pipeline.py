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
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import ZOUWUCODEConfig
from ..engine.loop import EngineLoop, TaskInterrupted, TurnLimitExceeded
from ..runtime import (create_provider, create_builtin_tools,
                       build_agent_engine)
from ..tools.registry import ToolRegistry
from ..sandbox.permission import PermissionManager
from ..agent.coordinator import AgentCoordinator
from ..project_memory import ProjectMemory
from .github import GitHubClient, GitHubError
from .workspace import WorktreeManager, WorkspaceError, validate_branch
from . import verifiers
from . import reviewer

logger = logging.getLogger("zouwucode.dev.pipeline")

# Prompt for the autonomous worker session.
# Design note: constraints live at the COGNITIVE layer (a self-check
# checklist the model runs before finishing) rather than as a pile of hard
# prohibitions — heavy restriction degrades model performance, while an
# explicit boundary checklist measurably improves edge-case coverage. The
# real enforcement is the multi-layer verification + independent reviewer
# downstream, so the prompt stays informative, not restrictive.
_SYSTEM_PROMPT = """You are ZOUWUCODE Dev, an autonomous software engineer \
working inside an isolated git worktree. Complete the assigned task end-to-end:
understand the code, implement the change, and verify it with the project's \
tests. Rules:
1. Stay within this worktree directory.
2. Never create commits yourself — verification and commit are handled by \
the harness after you finish.
3. Prefer minimal, focused changes that satisfy the task.
4. Before you finish, run a BOUNDARY SELF-CHECK and fix what it finds:
   - empty / None / zero / negative / very-large inputs
   - every external call (file, network, subprocess, API) has an error path
   - files/connections/locks are released on all paths (context managers)
   - shared state under concurrency is protected
   - works on BOTH Windows and POSIX (paths, encoding, line endings)
   - public API changes keep backward compatibility
   - no secrets, injection, or path traversal
5. When done, summarize: what changed, why, how it was verified, and any \
boundary cases you explicitly handled.
"""

# Explicit PLAN phase (PLAN → ACT → REFLECT, the canonical agent loop).
# Runs in the SAME engine so the plan lives in the append-only prefix —
# later rounds reference it for free, and cache stability is preserved.
_PLAN_PROMPT = """You are ZOUWUCODE Dev planning a change in an isolated \
git worktree. Given the task, inspect just enough of the code to produce a \
CONCISE ordered plan (3-8 steps) to complete it: which files to touch, what \
to implement, how to verify, and the boundary cases this task must handle. \
Do NOT edit any file in this phase. Output only the numbered plan."""


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
    review_summary: str = ""     # independent reviewer verdict (markdown)
    ci_status: str = ""          # "" | "passing" | "failing" | "timeout" | "skipped"


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

        Cost budget comes from config.engine.max_cost_usd (engine-enforced),
        optionally overridden per-task via _apply_budget (adaptive budget).
        Delegates to runtime.build_agent_engine — the same wiring the eval
        runner uses, so evaluation measures the real production stack.
        """
        engine, tools = build_agent_engine(self.config, Path.cwd())
        self._current_tools = tools
        return engine

    def _build_reviewer_engine(self) -> EngineLoop:
        """Fresh engine for the independent reviewer.

        Separate EngineLoop (own cache/stats) so the reviewer shares none of
        the implementer's conversation context — that isolation is what
        breaks the self-review bias. Runs in `plan` mode: the review is
        diff-based (the full unified diff is passed in the prompt), no tool
        execution happens, and the read-only whitelist guarantees the
        reviewer could not modify the code even if it tried.
        """
        cfg = self.config
        provider = create_provider(cfg)
        engine = EngineLoop(cfg, provider)
        engine.set_mode("plan")
        sandbox = PermissionManager(cfg.sandbox)
        sandbox.set_workspace(Path.cwd())
        tools = ToolRegistry()
        allowed = set(reviewer.REVIEWER_TOOL_WHITELIST)
        for t in create_builtin_tools(sandbox):
            if t.get_spec().name in allowed:
                tools.register(t)
        coordinator = AgentCoordinator(cfg, engine, tools)
        engine.set_tool_executor(coordinator.execute_tool)
        self._current_reviewer_tools = tools
        return engine

    def _apply_budget(self, engine: EngineLoop, escalations: int) -> None:
        """Adaptive cost budget: start at the dev base budget, double it per
        escalation. Keeps simple tasks cheap without starving complex ones.

        Mutates the per-process engine config (dev workers are separate
        processes, and the base is re-applied at every task start so an
        escalation never leaks into the next task).
        """
        base = self.dev_cfg.task_cost_budget_usd or self.config.engine.max_cost_usd
        if base <= 0:
            return  # budget breaker disabled
        engine.config.engine.max_cost_usd = base * (2 ** escalations)

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

    async def _verify(self) -> verifiers.VerificationReport:
        """Run the multi-layer verification pipeline in the worktree."""
        return await verifiers.run_verification(
            Path.cwd(),
            lint_command=self.dev_cfg.lint_command,
            typecheck_command=self.dev_cfg.typecheck_command,
            test_command=self.dev_cfg.test_command,
            security_command=self.dev_cfg.security_command,
            coverage_min=self.dev_cfg.coverage_min,
        )

    # ── CI linkage ─────────────────────────────────────────────────────────

    async def _await_ci(self, owner: str, repo: str, branch: str) -> str:
        """Poll GitHub checks for the pushed branch until they settle or the
        wait budget elapses. Returns passing | failing | timeout | skipped."""
        if not (self.dev_cfg.ci_check_enabled and owner and repo
                and self.github.has_token):
            return "skipped"
        waited = 0.0
        interval = max(1.0, self.dev_cfg.ci_poll_interval)
        empty_polls = 0
        while waited < self.dev_cfg.ci_wait_seconds:
            try:
                runs = await self.github.list_check_runs(owner, repo, branch)
            except GitHubError as exc:
                logger.warning("CI check poll failed: %s", exc)
                return "skipped"
            if not runs:
                # Checks register within seconds of a push; if none appear
                # after a few polls the repo simply has no CI — don't burn
                # the whole wait budget on every task.
                empty_polls += 1
                if empty_polls >= 3:
                    return "skipped"
                await asyncio.sleep(interval)
                waited += interval
                continue
            if any(r["status"] != "completed" for r in runs):
                await asyncio.sleep(interval)
                waited += interval
                continue
            if any(r.get("conclusion") not in ("success", None) for r in runs):
                return "failing"
            return "passing"
        return "timeout"

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
            # Adaptive budget: base budget per task; cost-abort doubles it
            # (bounded by dev.budget_escalations) instead of failing outright.
            self._apply_budget(engine, escalations=0)
            logger.info("Dev task %s started in worktree %s", task_id, ws.path)

            # PLAN phase (explicit, same session → plan stays in the prefix)
            if self.dev_cfg.plan_enabled:
                plan = await self._run_plan(engine, prompt)
                logger.info("Dev task %s planned (%d chars)", task_id, len(plan))

            # Round 0: autonomous implementation
            summary = await self._implement_with_budget(engine, prompt)
            result.summary = summary

            # Multi-layer verification loop: lint → typecheck → test(+cov)
            # → security. Each failed layer is fed back with its own label,
            # so the agent gets targeted, informative feedback rather than
            # a single opaque "tests failed".
            for iteration in range(self.dev_cfg.verify_retries + 1):
                report = await self._verify()
                result.verification_output = report.render_feedback()
                result.iterations = iteration + 1
                if report.passed:
                    break
                if iteration >= self.dev_cfg.verify_retries:
                    result.error = ("verification failed after "
                                    f"{iteration + 1} attempt(s)")
                    await self._report_failure(
                        number, result.verification_output, prompt, post_report)
                    return result
                logger.info("Verification failed — iteration %d, feeding back",
                            iteration + 1)
                feedback = (
                    "\n\n## Verification result (FAILED)\n"
                    "The multi-layer checks below did not pass after your "
                    "changes. First REFLECT: which step of your plan broke, "
                    "and does the plan need revision? Then fix every FAILED "
                    "layer (SKIPPED layers are not blocking):\n\n"
                    f"{result.verification_output}\n\n"
                    "Fix the remaining issues and re-run to completion."
                )
                summary = await self._implement_with_budget(
                    engine, prompt, extra_context=feedback)
                result.summary = summary
            else:
                # loop exhausted without break → last verification run failed
                return result

            # Local commit FIRST (still inside the isolated worktree, nothing
            # pushed) so the reviewer can see a proper base...HEAD diff.
            committed = await self.worktrees.commit_all(
                ws, f"dev: {prompt.splitlines()[0][:72]} [ZOUWUCODE]")
            if not committed:
                result.error = "agent finished but produced no file changes"
                await self._report_failure(number, "no changes produced",
                                           prompt, post_report)
                return result

            # Independent review loop — fresh engine, read-only tools, no
            # shared context with the implementer. request_changes findings
            # are fed back; bounded by dev.review_max_rounds.
            review_outcome = None
            if self.dev_cfg.review_enabled:
                for review_round in range(self.dev_cfg.review_max_rounds + 1):
                    diff_text = await self.worktrees.diff_text(ws, ws.base_ref)
                    review_engine = self._build_reviewer_engine()
                    review_outcome = await reviewer.review_diff(
                        review_engine, prompt, diff_text,
                        tool_schemas=self._current_reviewer_tools.get_schemas())
                    if review_outcome.approved:
                        break
                    if review_round >= self.dev_cfg.review_max_rounds:
                        break  # proceed to PR; verdict goes into the body
                    fix_summary = await self._implement_with_budget(
                        engine, prompt,
                        extra_context=reviewer.format_review_feedback(
                            review_outcome))
                    result.summary = fix_summary
                    await self.worktrees.commit_all(
                        ws, f"dev: address review round {review_round + 1} "
                            f"[ZOUWUCODE]")
                result.review_summary = reviewer.format_review_for_pr(
                    review_outcome) if review_outcome else ""

            # Push + Draft PR + CI linkage.
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

            # CI linkage: the Draft PR's checks run in the REAL CI matrix
            # (OS/python versions the local worktree cannot reproduce).
            # Non-blocking by design — the PR stays Draft either way; a
            # failing/timed-out CI is reported back for the human reviewer.
            if owner and repo:
                result.ci_status = await self._await_ci(owner, repo, branch)
                if result.ci_status in ("failing", "timeout") and number:
                    await self._report_ci(result.ci_status, owner, repo, number)
            self._harvest_learning(task_id, result)
            if post_report and not (owner and repo and number):
                logger.info("Local dev task done (no GitHub link): %s",
                            result.pr_url or branch)
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

    async def _run_plan(self, engine: EngineLoop, prompt: str) -> str:
        """Explicit PLAN phase in the SAME engine session.

        The plan becomes part of the append-only prefix, so every later
        round (and every verification-feedback round) can see and follow
        it — no extra context plumbing needed. Goes through _run_agent so
        the session plumbing (and test stubs) stay in one place.
        """
        return await self._run_agent(
            engine, prompt, extra_context=f"\n\n{_PLAN_PROMPT}")

    async def _implement_with_budget(self, engine: EngineLoop, prompt: str,
                                     extra_context: str = "") -> str:
        """Agent run with adaptive cost budget.

        A cost-abort (TurnLimitExceeded mentioning cost) doubles the budget
        and continues the SAME conversation once, up to
        dev.budget_escalations times — complex tasks get room to finish
        while simple ones never see inflated limits. Non-cost limits
        (rounds/timeout/interrupt) propagate untouched.
        """
        escalations = 0
        while True:
            try:
                return await self._run_agent(engine, prompt,
                                             extra_context=extra_context)
            except TurnLimitExceeded as exc:
                if ("cost" not in str(exc).lower()
                        or not self.dev_cfg.adaptive_budget
                        or escalations >= self.dev_cfg.budget_escalations):
                    raise
                escalations += 1
                self._apply_budget(engine, escalations=escalations)
                logger.warning(
                    "Cost budget hit — escalating %d/%d (new budget $%.2f)",
                    escalations, self.dev_cfg.budget_escalations,
                    engine.config.engine.max_cost_usd)
                prompt = ("Continue the assigned task from exactly where you "
                          "stopped. The cost budget has been increased. "
                          "Do not redo finished work; finish the remaining "
                          "steps and produce your final summary.")
                extra_context = ""

    async def _report_ci(self, status: str, owner: str, repo: str,
                         number: int) -> None:
        """Comment on the PR when real CI disagrees with local verification."""
        try:
            await self.github.comment_issue(
                owner, repo, number,
                f"🤖 **ZOUWUCODE dev**：本地多层验证已通过，但真实 CI 状态为 "
                f"`{status}`。Draft PR 已保持草稿，请人工确认后再合并。",
            )
        except GitHubError as exc:
            logger.warning("Failed to post CI status comment: %s", exc)

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
    parts = [
        f"## 🤖 ZOUWUCODE dev — 自主完成\n\n"
        f"**任务**\n> {prompt.splitlines()[0][:500]}\n\n"
        f"**实现摘要**\n{result.summary[:3000]}\n\n"
        f"**多层验证**（{result.iterations} 轮）\n```\n"
        f"{result.verification_output[-1500:]}\n```\n\n"
        f"**变更文件**\n```\n{diffstat[:2000]}\n```\n\n"
        f"**成本** ${result.cost_usd:.4f}\n",
    ]
    if result.review_summary:
        parts.append(f"\n{result.review_summary}\n")
    if result.ci_status == "failing":
        parts.append("\n> ⚠️ 真实 CI 检查未通过（本地验证已通过）——"
                     "合并前请先修复 CI。\n")
    elif result.ci_status == "timeout":
        parts.append("\n> ⏳ 等待 CI 结果超时，合并前请确认 CI 状态。\n")
    parts.append(
        "\n⚠️ 本 PR 由 AI 自主生成并经独立 AI 审查，处于 Draft 状态，"
        "请人工 review 后合并。")
    return "".join(parts)
