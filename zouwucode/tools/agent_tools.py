"""Agent delegation tool — lets the main model decompose and delegate work.

The model calls the ``task`` tool with a list of subtasks; each subtask is
executed by an isolated SubAgent (own engine/cache) in parallel, and the
combined reports are returned as one tool result so the main agent can
continue reasoning over them.
"""

import json
from typing import Optional

from .base import BaseTool, ToolSpec, ToolResult


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
            return ToolResult(success=False, output="", error=str(exc))
        except Exception as exc:  # noqa: BLE001 — report, don't crash the engine
            return ToolResult(success=False, output="",
                              error=f"Sub-agent orchestration failed: {exc}")

        lines = []
        for agent in agents:
            lines.append(f"── [{agent.role}] {agent.id} — {agent.status.value} "
                         f"({agent.duration:.1f}s)")
            if agent.status.value == "completed" and agent.result is not None:
                content = (agent.result.content or "").strip()
                lines.append(content if content else "(no output)")
            else:
                lines.append(f"(failed: {agent.error or 'unknown error'})")
            lines.append("")

        return ToolResult(success=True, output="\n".join(lines).strip())
