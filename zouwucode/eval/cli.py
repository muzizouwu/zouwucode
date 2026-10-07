"""CLI front-end for the eval harness — `zouwucode eval`.

    zouwucode eval                       # run built-in sample tasks
    zouwucode eval --tasks my_tasks/     # run a task directory
    zouwucode eval --task fix-bug        # run one task by name
    zouwucode eval --list                # list available tasks
"""

import argparse
import asyncio
import logging
import time
from pathlib import Path

from ..config import ZOUWUCODEConfig
from ..logging_setup import setup_logging
from .runner import load_tasks, run_suite

logger = logging.getLogger("zouwucode.eval.cli")

_BUILTIN_TASKS = Path(__file__).resolve().parent / "tasks"


def _default_results_dir(config: ZOUWUCODEConfig) -> Path:
    return Path(config.data_dir or ".") / "eval_runs"


def eval_main(argv: list[str]) -> int:
    """Entry for `zouwucode eval ...`. argv excludes the 'eval' token."""
    parser = argparse.ArgumentParser(
        prog="zouwucode eval",
        description="Task-level evaluation harness (pass-rate + cost)")
    parser.add_argument("--tasks", type=str, default="",
                        help="任务目录（默认使用内置 eval 示例任务）")
    parser.add_argument("--task", type=str, default="",
                        help="只运行指定名称的任务")
    parser.add_argument("--list", action="store_true", help="列出可用任务")
    parser.add_argument("--config", "-c", type=str, default=None)
    args = parser.parse_args(argv)

    config = ZOUWUCODEConfig.load(Path(args.config) if args.config else None)
    setup_logging(config.data_dir, config.log_level)

    tasks_dir = Path(args.tasks) if args.tasks else _BUILTIN_TASKS
    if not tasks_dir.exists():
        print(f"  ✗ 任务目录不存在: {tasks_dir}")
        return 2
    tasks = load_tasks(tasks_dir)
    if args.task:
        tasks = [t for t in tasks if t.name == args.task]
        if not tasks:
            print(f"  ✗ 未找到任务: {args.task}")
            return 2

    if args.list:
        for t in tasks:
            print(f"  • {t.name}  (checks={len(t.checks)}, timeout={t.timeout:.0f}s)")
        print(f"\n  共 {len(tasks)} 个任务，目录: {tasks_dir}")
        return 0

    if not tasks:
        print("  ✗ 任务目录为空")
        return 2

    base = _default_results_dir(config) / time.strftime("%Y%m%d-%H%M%S")
    print(f"\n  ▶ eval：{len(tasks)} 个任务，工作区 {base}\n")
    results = asyncio.run(run_suite(tasks, config, base))

    passed = sum(1 for r in results if r.passed)
    total_cost = sum(r.cost_usd for r in results)
    for r in results:
        mark = "✅" if r.passed else "❌"
        line = f"  {mark} {r.name:<24} {r.duration_s:6.1f}s  ${r.cost_usd:.4f}"
        if r.error:
            line += f"  error: {r.error[:80]}"
        elif not r.passed:
            bad = [c for c in r.checks if not c["passed"]]
            line += f"  failed: {bad[0]['check'] if bad else '?'}"
        print(line)
    rate = passed / len(results) * 100
    print(f"\n  通过率 {passed}/{len(results)} = {rate:.0f}%   总成本 ${total_cost:.4f}\n")
    return 0 if passed == len(results) else 1
