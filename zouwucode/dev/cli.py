"""CLI front-end for dev mode — single task, queue, workers, watch.

    zouwucode dev <issue-url|task-text>     # run one task now (foreground)
    zouwucode dev --queue <issue-url>       # enqueue a task
    zouwucode dev --workers N               # drain queue with N worker processes
    zouwucode dev --watch owner/repo        # poll labeled issues, auto-enqueue+run
    zouwucode dev --status                  # queue stats + recent tasks

Parallelism model: each worker is a separate `python -m zouwucode dev
--worker` subprocess. Process isolation gives us per-task CWD (worktrees),
crash containment, and clean Ctrl+C semantics for free.
"""

import argparse
import asyncio
import logging
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from ..config import ZOUWUCODEConfig
from ..logging_setup import setup_logging
from .github import GitHubClient, GitHubError
from .pipeline import DevPipeline, DevResult
from .queue import DevQueue

logger = logging.getLogger("zouwucode.dev.cli")


def _repo_root() -> Path:
    return Path.cwd()


def _queue_path(config: ZOUWUCODEConfig) -> Path:
    return Path(config.data_dir) / "dev_queue.sqlite"


def _owner_repo_from_remote() -> Optional[tuple[str, str]]:
    """Parse owner/repo from `git remote get-url origin`."""
    try:
        out = subprocess.run(["git", "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", out)
        if m:
            return m.group(1), m.group(2)
    except Exception:
        pass
    return None


# ── Single foreground task ───────────────────────────────────────────────────

async def run_single(task_ref: str, config: ZOUWUCODEConfig) -> DevResult:
    pipeline = DevPipeline(config, _repo_root())
    result = await pipeline.run(task_ref)
    _print_result(result)
    return result


def _print_result(r: DevResult) -> None:
    print()
    if r.success:
        print(f"  ✅ dev 任务完成（{r.iterations} 轮验证，成本 ${r.cost_usd:.4f}）")
        if r.pr_url:
            print(f"  🔗 Draft PR: {r.pr_url}")
        else:
            print(f"  🌿 分支: {r.branch}（本地 worktree，未关联 GitHub issue）")
        if r.files_changed:
            print(f"  📁 变更 {len(r.files_changed)} 个文件: "
                  f"{', '.join(r.files_changed[:8])}")
        if r.ci_status and r.ci_status != "skipped":
            print(f"  🏳 CI 状态: {r.ci_status}")
    else:
        print(f"  ❌ dev 任务失败: {r.error}")
    print()


# ── Worker process (claims tasks from the queue in a loop) ──────────────────

async def run_worker(config: ZOUWUCODEConfig, once: bool = False) -> int:
    """Drain the queue. Returns number of tasks processed."""
    queue = DevQueue(_queue_path(config))
    queue.requeue_stale()
    processed = 0
    while True:
        task = queue.claim_next()
        if task is None:
            if once:
                break
            # Idle: exit so the supervisor can manage liveness cheaply.
            break
        task_id, ref, timeout = task["id"], task["task_ref"], task["timeout"]
        logger.info("Worker claimed task %s: %s", task_id, ref[:80])
        pipeline = DevPipeline(config, _repo_root())
        try:
            result: DevResult = await asyncio.wait_for(
                pipeline.run(ref), timeout=timeout
            )
            queue.finish(task_id, success=result.success,
                         result={"branch": result.branch, "pr_url": result.pr_url,
                                 "iterations": result.iterations,
                                 "cost_usd": result.cost_usd},
                         error=result.error)
        except asyncio.TimeoutError:
            queue.finish(task_id, success=False,
                         error=f"task timed out after {timeout:.0f}s")
        except Exception as exc:  # noqa: BLE001 — worker must survive task crashes
            logger.exception("Worker task %s crashed", task_id)
            queue.finish(task_id, success=False, error=str(exc))
        processed += 1
        if once:
            break
    return processed


# ── Supervisor: spawn N worker subprocesses ─────────────────────────────────

def spawn_workers(config: ZOUWUCODEConfig, n: int, *, drain: bool = True) -> int:
    """Run N worker subprocesses. With drain=True each exits when the queue
    empties; otherwise loops are kept alive by the caller (watch mode)."""
    procs = []
    for i in range(n):
        args = [sys.executable, "-m", "zouwucode", "dev", "--worker"]
        if drain:
            args.append("--once")
        procs.append(subprocess.Popen(args, cwd=str(_repo_root())))
        logger.info("Spawned worker #%d (pid=%d)", i + 1, procs[-1].pid)
    rc = 0
    try:
        for p in procs:
            p.wait()
            rc = max(rc, p.returncode)
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()
    return rc


# ── Watch mode: labeled issues → queue → workers ─────────────────────────────

async def run_watch(config: ZOUWUCODEConfig, repo_slug: Optional[str],
                    interval: float, workers: int) -> None:
    """Poll open issues labeled `dev.watch_label`, enqueue, run workers."""
    gh = GitHubClient.from_config(config.github)
    if not gh.has_token:
        print("  ✗ watch 模式需要 GitHub token（GITHUB_TOKEN 或 config.github.token）")
        return
    if repo_slug:
        m = re.match(r"^([\w.-]+)/([\w.-]+)$", repo_slug)
        if not m:
            print(f"  ✗ 仓库格式应为 owner/repo，收到: {repo_slug}")
            return
        owner, repo = m.group(1), m.group(2)
    else:
        parsed = _owner_repo_from_remote()
        if not parsed:
            print("  ✗ 当前目录没有 origin 远程，请显式指定 owner/repo")
            return
        owner, repo = parsed

    label = config.dev.watch_label
    queue = DevQueue(_queue_path(config))
    print(f"  👀 watch 启动 — {owner}/{repo} label={label} 每 {interval:.0f}s 轮询，"
          f"Ctrl+C 停止")
    seen: set[int] = set()
    try:
        while True:
            try:
                issues = await gh.list_issues_by_label(owner, repo, label, limit=10)
            except GitHubError as exc:
                logger.error("watch poll failed: %s", exc)
                issues = []
            fresh = [i for i in issues if i["number"] not in seen]
            for issue in fresh:
                seen.add(issue["number"])
                url = issue["html_url"]
                task_id = queue.submit(url)
                logger.info("watch enqueued issue #%d → task %s",
                            issue["number"], task_id)
                try:
                    await gh.remove_label(owner, repo, issue["number"], label)
                except GitHubError:
                    pass  # best-effort; claim guard is the seen set
            if fresh:
                spawn_workers(config, workers=min(workers, len(fresh)), drain=True)
            await asyncio.sleep(interval)
    except KeyboardInterrupt:
        print("\n  watch 已停止。")


# ── Status ───────────────────────────────────────────────────────────────────

def print_status(config: ZOUWUCODEConfig) -> None:
    queue = DevQueue(_queue_path(config))
    stats = queue.stats()
    print(f"\n  Dev 队列: pending={stats['pending']} running={stats['running']} "
          f"done={stats['done']} failed={stats['failed']}\n")
    for t in queue.list_tasks(limit=10):
        ts = time.strftime("%m-%d %H:%M", time.localtime(t["created_at"]))
        err = f" — {t['error'][:60]}" if t["error"] else ""
        print(f"    {t['id']}  {t['status']:<8} {ts}  {t['task_ref'][:60]}{err}")
    print()


# ── Argument routing (called from zouwucode/__main__.py) ─────────────────────

def dev_main(argv: list[str]) -> int:
    """Entry for `zouwucode dev ...`. argv excludes the 'dev' token."""
    parser = argparse.ArgumentParser(prog="zouwucode dev",
                                     description="Devin-style autonomous dev workflow")
    parser.add_argument("task", nargs="?", help="issue URL / owner/repo#N / 任务描述")
    parser.add_argument("--config", "-c", type=str, default=None)
    parser.add_argument("--queue", action="store_true", help="提交任务到队列")
    parser.add_argument("--workers", type=int, default=0, help="启动 N 个 worker 进程排空队列")
    parser.add_argument("--watch", nargs="?", const="", metavar="OWNER/REPO",
                        help="轮询带标签 issue 自动认领执行")
    parser.add_argument("--interval", type=float, default=60.0, help="watch 轮询间隔秒")
    parser.add_argument("--status", action="store_true", help="查看队列状态")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    config = ZOUWUCODEConfig.load(Path(args.config) if args.config else None)
    setup_logging(config.data_dir, config.log_level)

    if args.status:
        print_status(config)
        return 0

    if args.worker:
        rc = asyncio.run(run_worker(config, once=args.once))
        return 0

    if args.watch is not None:
        asyncio.run(run_watch(config, args.watch or None,
                              args.interval,
                              max(1, config.dev.max_concurrent_tasks)))
        return 0

    if args.queue:
        if not args.task:
            print("  ✗ --queue 需要任务参数（issue URL 或任务描述）")
            return 2
        queue = DevQueue(_queue_path(config))
        task_id = queue.submit(args.task)
        print(f"  ✓ 已入队: {task_id}  （用 `zouwucode dev --workers N` 执行）")
        return 0

    if args.workers > 0:
        return spawn_workers(config, args.workers)

    if args.task:
        result = asyncio.run(run_single(args.task, config))
        return 0 if result.success else 1

    parser.print_help()
    return 0
