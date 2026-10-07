"""Task-level evaluation harness — the missing feedback loop.

Industry consensus (2026): the scaffold around the model, not the model,
drives agent quality — and you cannot improve a scaffold you cannot
measure. This package runs the REAL agent stack (same engine/sandbox/
tools as production) against a set of deterministic tasks with executable
acceptance checks, and reports pass-rate + cost per task.

Task file (YAML, one per task, in a tasks dir):

    name: fix-off-by-one
    prompt: |
      calc.py has an off-by-one bug: sum_range(1,3) returns 5 not 6.
      Fix it. Do not change the tests.
    setup:                       # files created in a temp workspace
      calc.py: |
          def sum_range(a, b): return sum(range(a, b))
    checks:                      # ALL must pass for the task to pass
      - type: file_contains
        path: calc.py
        text: "range(a, b + 1)"
      - type: python_eval        # runs in the workspace with the agent's code
        expr: "import calc; calc.sum_range(1,3) == 6"
      - type: command_pass       # shell exit code 0
        command: python -m pytest -q

Checks are deterministic and cheap — the LLM only does the work, never
the grading. Run via `zouwucode eval`.
"""

from .runner import EvalTask, TaskResult, run_task, load_tasks
from .checks import run_checks

__all__ = ["EvalTask", "TaskResult", "run_task", "load_tasks", "run_checks"]
