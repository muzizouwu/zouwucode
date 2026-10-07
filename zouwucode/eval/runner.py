"""Eval runner — real agent stack, deterministic grading, measured pass rate."""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from ..config import ZOUWUCODEConfig
from ..runtime import build_agent_engine
from .checks import run_checks

logger = logging.getLogger("zouwucode.eval")

_EVAL_SYSTEM_PROMPT = """You are ZOUWUCODE, an autonomous coding agent \
working in a small isolated workspace. Complete the task exactly as \
described. Make minimal, correct changes. Use file tools or python_exec \
to inspect and modify the workspace. When the task is done, stop.
"""


@dataclass
class EvalTask:
    name: str
    prompt: str
    setup: dict = field(default_factory=dict)      # relpath -> file content
    checks: list = field(default_factory=list)     # check dicts (see checks.py)
    timeout: float = 300.0

    @classmethod
    def from_dict(cls, data: dict, source: str = "") -> "EvalTask":
        if not data.get("name") or not data.get("prompt"):
            raise ValueError(f"eval task in {source or '<dict>'} needs "
                             "'name' and 'prompt'")
        return cls(
            name=str(data["name"]),
            prompt=str(data["prompt"]),
            setup=dict(data.get("setup") or {}),
            checks=list(data.get("checks") or []),
            timeout=float(data.get("timeout", 300.0)),
        )


@dataclass
class TaskResult:
    name: str
    passed: bool
    checks: list = field(default_factory=list)
    error: str = ""
    cost_usd: float = 0.0
    duration_s: float = 0.0
    agent_output: str = ""


def load_tasks(tasks_dir: Path) -> list[EvalTask]:
    """Load *.yaml / *.yml task files (each a single task mapping)."""
    tasks: list[EvalTask] = []
    for f in sorted(Path(tasks_dir).glob("*.y*ml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        if isinstance(data, list):
            tasks.extend(EvalTask.from_dict(d, str(f)) for d in data)
        else:
            tasks.append(EvalTask.from_dict(data, str(f)))
    return tasks


async def run_task(task: EvalTask, config: ZOUWUCODEConfig,
                   workspace: Optional[Path] = None,
                   engine_factory=None) -> TaskResult:
    """Execute one eval task end-to-end in an isolated temp workspace.

    engine_factory: callable(config, workspace) -> (engine, tools);
    defaults to runtime.build_agent_engine (the real production stack).
    Injectable for tests.
    """
    ws = Path(workspace) if workspace else Path(
        os.path.join(config.data_dir or ".", "eval", task.name))
    ws.mkdir(parents=True, exist_ok=True)
    for rel, content in task.setup.items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(content), encoding="utf-8")

    result = TaskResult(name=task.name, passed=False)
    old_cwd = Path.cwd()
    try:
        os.chdir(ws)
        factory = engine_factory or build_agent_engine
        engine, tools = factory(config, ws)
        messages = [
            {"role": "system", "content": _EVAL_SYSTEM_PROMPT},
            {"role": "user", "content": task.prompt},
        ]
        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                engine.run(messages=messages, tools=tools.get_schemas()),
                timeout=task.timeout)
            result.agent_output = (response.content or "")[:2000]
        except asyncio.TimeoutError:
            result.error = f"agent timed out after {task.timeout:.0f}s"
            return result
        except Exception as exc:  # noqa: BLE001 — eval must grade, not crash
            result.error = f"agent run failed: {exc}"
            return result
        finally:
            result.duration_s = time.monotonic() - start
            result.cost_usd = engine.stats.total_cost

        if not task.checks:
            result.error = "task defines no checks"
            return result
        result.checks = await run_checks(task.checks, ws)
        result.passed = all(c["passed"] for c in result.checks)
        return result
    finally:
        os.chdir(old_cwd)


async def run_suite(tasks: list[EvalTask], config: ZOUWUCODEConfig,
                    base_dir: Path, engine_factory=None) -> list[TaskResult]:
    """Run tasks sequentially (deterministic; parallelism is a later axis)."""
    results = []
    for t in tasks:
        ws = base_dir / t.name
        logger.info("eval: running %s", t.name)
        results.append(await run_task(t, config, workspace=ws,
                                      engine_factory=engine_factory))
    return results
