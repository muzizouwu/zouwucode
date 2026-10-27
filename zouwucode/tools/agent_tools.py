"""Agent delegation tool — lets the main model decompose and delegate work.

The model calls the ``task`` tool with a list of subtasks; each subtask is
executed by an isolated SubAgent (own engine/cache) in parallel, and the
combined reports are returned as one tool result so the main agent can
continue reasoning over them.
"""

import logging
import time
from pathlib import Path
from typing import Optional

from .base import BaseTool, ToolSpec, ToolResult
from ..errors import describe_exc

logger = logging.getLogger("zouwucode.tools.task")

# Eight verbose workers used to dump ~10k tokens straight into the parent
# context per call — the single biggest avoidable cost spike. Keep the merged
# report within a predictable budget and persist the full text to disk so it
# stays retrievable with the read tool.
_MAX_TASK_REPORT_CHARS = 12_000
_MIN_PER_AGENT_CHARS = 600


def _clip(text: str, budget: int) -> str:
    """Keep the head and tail of *text* within *budget* characters.

    Head/tail clipping (rather than head-only) keeps both the worker's
    conclusion and its supporting detail, which is where the useful signal
    usually lives.
    """
    if budget <= 0 or not text:
        return ""
    if len(text) <= budget:
        return text
    marker = "\n… [truncated] …\n"
    if budget <= len(marker) + 40:
        return text[:budget]
    head = int((budget - len(marker)) * 0.7)
    tail = budget - len(marker) - head
    return text[:head] + marker + text[-tail:]


def _save_full_report(manager, text: str) -> Optional[str]:
    """Best-effort dump of the untruncated reports; returns the path or None."""
    try:
        raw_dir = getattr(getattr(manager, "config", None), "data_dir", "") or "."
        out_dir = Path(raw_dir) / "agent_reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"task-{time.strftime('%Y%m%d-%H%M%S')}.md"
        path.write_text(text, encoding="utf-8")
        return str(path)
    except Exception as exc:
        logger.debug("could not save full sub-agent report: %s", describe_exc(exc))
        return None


class TaskTool(BaseTool):
    """Delegate subtasks to isolated parallel sub-agents."""

    def __init__(self, manager, sandbox: Optional[object] = None):
        # manager: SubAgentManager (avoid import cycle; duck-typed)
        super().__init__(sandbox)
        self._manager = manager

    def get_spec(self) -> ToolSpec:
        return ToolSpec(
            name="task",
            description=(
                "Decompose work and delegate subtasks to isolated sub-agents "
                "running in parallel. Each subtask gets its own context window. "
                "Use for independent, parallelizable units of work (research, "
                "review, scanning, implementation steps). Returns every "
                "sub-agent's report."
            ),
            parameters={
                "tasks": {
                    "type": "array",
                    "description": "Subtasks to delegate (1-8 items)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {
                                "type": "string",
                                "description": "Worker role name, e.g. 'researcher', 'reviewer'",
                            },
                            "instructions": {
                                "type": "string",
                                "description": "System instructions for this worker",
                            },
                            "task": {
                                "type": "string",
                                "description": "The concrete task to perform",
                            },
                            "tools": {
                                "type": "array",
                                "description": "Optional tool whitelist for this worker",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["task"],
                    },
                },
            },
            required=["tasks"],
        )

    async def execute(self, tasks: list, **_) -> ToolResult:
        if not isinstance(tasks, list) or not tasks:
            return ToolResult(success=False, output="",
                              error="tasks must be a non-empty list")
        for i, spec in enumerate(tasks):
            if not isinstance(spec, dict) or not str(spec.get("task", "")).strip():
                return ToolResult(
                    success=False, output="",
                    error=(f"tasks[{i}] must be an object with a non-empty "
                           f"'task' field"),
                )

        try:
            agents = await self._manager.run_parallel(tasks)
        except (RuntimeError, ValueError) as exc:
            # e.g. max_agents exceeded / malformed specs
            return ToolResult(success=False, output="", error=describe_exc(exc))
        except Exception as exc:  # noqa: BLE001 — report, don't crash the engine
            return ToolResult(success=False, output="",
                              error=f"Sub-agent orchestration failed: {describe_exc(exc)}")

        lines = []
        bodies = []
        for agent in agents:
            wt = getattr(agent, "worktree", None)
            tag = f"  ↳ worktree: {wt.path} (branch {wt.branch})" if wt else ""
            header = (f"── [{agent.role}] {agent.id} — {agent.status.value} "
                      f"({agent.duration:.1f}s){tag}")
            if agent.status.value == "completed" and agent.result is not None:
                content = (agent.result.content or "").strip()
                body = content if content else "(no output)"
            else:
                body = f"(failed: {agent.error or 'unknown error'})"
            lines.append(header)
            lines.append(body)
            lines.append("")
            bodies.append(body)

        full = "\n".join(lines).strip()
        if len(full) <= _MAX_TASK_REPORT_CHARS:
            return ToolResult(success=True, output=full)

        # Budget the merged report; persist the untruncated text for later.
        per = max(_MIN_PER_AGENT_CHARS,
                  _MAX_TASK_REPORT_CHARS // max(1, len(agents)))
        slim = []
        for agent, body in zip(agents, bodies):
            wt = getattr(agent, "worktree", None)
            tag = f"  ↳ worktree: {wt.path} (branch {wt.branch})" if wt else ""
            slim.append(f"── [{agent.role}] {agent.id} — {agent.status.value} "
                        f"({agent.duration:.1f}s){tag}")
            slim.append(_clip(body, per))
            slim.append("")
        merged = "\n".join(slim).strip()

        path = _save_full_report(self._manager, full)
        if path:
            merged += (f"\n\n(full reports saved to {path} — "
                       f"use the read tool if you need the omitted detail)")
        else:
            merged += ("\n\n(full reports could not be saved; "
                       "re-delegate with narrower tasks if detail is needed)")
        return ToolResult(success=True, output=merged)
