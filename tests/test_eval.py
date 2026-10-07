"""Tests for the eval harness — deterministic checks + graded runner."""

import asyncio
from pathlib import Path

import pytest

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.eval import EvalTask, load_tasks, run_checks, run_task
from zouwucode.eval.runner import TaskResult
from zouwucode.engine.providers.base import BaseProvider, ModelResponse

TASKS_DIR = Path(__file__).resolve().parent.parent / "zouwucode" / "eval" / "tasks"


class TestChecks:
    def test_file_contains_and_absent(self, tmp_path):
        (tmp_path / "a.py").write_text("hello world", encoding="utf-8")
        results = asyncio.run(run_checks([
            {"type": "file_contains", "path": "a.py", "text": "hello"},
            {"type": "file_contains", "path": "a.py", "text": "nope"},
            {"type": "file_absent", "path": "b.py"},
        ], tmp_path))
        assert [r["passed"] for r in results] == [True, False, True]

    def test_python_eval_truthy_and_falsy(self, tmp_path):
        (tmp_path / "m.py").write_text("VALUE = 6", encoding="utf-8")
        results = asyncio.run(run_checks([
            {"type": "python_eval", "expr": "__import__('m').VALUE == 6"},
            {"type": "python_eval", "expr": "__import__('m').VALUE == 5"},
        ], tmp_path))
        assert results[0]["passed"] is True
        assert results[1]["passed"] is False

    def test_command_pass(self, tmp_path):
        results = asyncio.run(run_checks([
            {"type": "command_pass", "command": "python -c pass"},
            {"type": "command_pass", "command": "python -c 'raise SystemExit(1)'"},
        ], tmp_path))
        assert [r["passed"] for r in results] == [True, False]

    def test_unknown_check_fails_loudly(self, tmp_path):
        results = asyncio.run(run_checks([{"type": "bogus"}], tmp_path))
        assert results[0]["passed"] is False


class TestTaskLoading:
    def test_builtin_tasks_load(self):
        tasks = load_tasks(TASKS_DIR)
        assert len(tasks) >= 3
        for t in tasks:
            assert t.name and t.prompt and t.checks and t.setup

    def test_from_dict_validation(self):
        with pytest.raises(ValueError):
            EvalTask.from_dict({"prompt": "no name"})


class _FakeFixer(BaseProvider):
    """Pretends to be the agent: edits calc.py correctly, then stops."""

    def __init__(self):
        super().__init__({"api_key": "sk-test"})
        self.round = 0

    async def chat_stream(self, messages, tools=None, temperature=0.0,
                          max_tokens=65536, stream_thinking=True):
        import json
        self.round += 1
        if self.round == 1:
            args = json.dumps({
                "file_path": "calc.py",
                "content": "def sum_range(a, b):\n    return sum(range(a, b + 1))\n",
            })
            yield ModelResponse(content="", tool_calls=[
                type("TC", (), {"id": "w1", "name": "write",
                                "arguments": args})()])
            yield ModelResponse(content="", tool_calls=[], usage={})
        else:
            yield ModelResponse(content="fixed")
            yield ModelResponse(content="", tool_calls=[], usage={})

    async def chat(self, messages, tools=None, temperature=0.0,
                   max_tokens=65536):
        return ModelResponse(content="fixed")


class TestRunTask:
    def test_passes_with_fixing_agent(self, tmp_path):
        """Full loop: setup files → (fake) agent fixes → checks grade pass."""
        task = EvalTask(
            name="off-by-one",
            prompt="fix sum_range",
            setup={"calc.py": "def sum_range(a, b):\n    return sum(range(a, b))\n"},
            checks=[{"type": "python_eval",
                     "expr": "__import__('calc').sum_range(1, 3) == 6"}],
        )
        cfg = ZOUWUCODEConfig()
        cfg.engine.max_tool_rounds = 5

        def factory(config, workspace):
            from zouwucode.runtime import build_agent_engine
            engine, tools = build_agent_engine(config, workspace,
                                               with_subagents=False)
            # swap provider by rebuilding engine with fake provider
            from zouwucode.engine.loop import EngineLoop
            engine = EngineLoop(config, _FakeFixer())
            engine.set_mode("yolo")
            from zouwucode.agent.coordinator import AgentCoordinator
            coord = AgentCoordinator(config, engine, tools)
            engine.set_tool_executor(coord.execute_tool)
            return engine, tools

        result = asyncio.run(run_task(task, cfg, workspace=tmp_path / "ws",
                                      engine_factory=factory))
        assert isinstance(result, TaskResult)
        assert result.passed, result.checks
        assert result.agent_output == "fixed"

    def test_fails_with_doing_agent(self, tmp_path):
        """Agent that does nothing → behavior check fails (no false pass)."""
        task = EvalTask(
            name="off-by-one",
            prompt="fix sum_range",
            setup={"calc.py": "def sum_range(a, b):\n    return sum(range(a, b))\n"},
            checks=[{"type": "python_eval",
                     "expr": "__import__('calc').sum_range(1, 3) == 6"}],
        )
        cfg = ZOUWUCODEConfig()
        cfg.engine.max_tool_rounds = 5

        class _Noop(BaseProvider):
            def __init__(self):
                super().__init__({"api_key": "sk-test"})

            async def chat_stream(self, messages, tools=None, temperature=0.0,
                                  max_tokens=65536, stream_thinking=True):
                yield ModelResponse(content="done thinking")
                yield ModelResponse(content="", tool_calls=[], usage={})

            async def chat(self, messages, tools=None, temperature=0.0,
                           max_tokens=65536):
                return ModelResponse(content="done thinking")

        def factory(config, workspace):
            from zouwucode.engine.loop import EngineLoop
            from zouwucode.runtime import create_builtin_tools
            from zouwucode.tools.registry import ToolRegistry
            from zouwucode.sandbox.permission import PermissionManager
            from zouwucode.agent.coordinator import AgentCoordinator
            sandbox = PermissionManager(config.sandbox)
            sandbox.set_workspace(workspace)
            tools = ToolRegistry()
            tools.register_all(create_builtin_tools(sandbox, config))
            engine = EngineLoop(config, _Noop())
            engine.set_mode("yolo")
            coord = AgentCoordinator(config, engine, tools)
            engine.set_tool_executor(coord.execute_tool)
            return engine, tools

        result = asyncio.run(run_task(task, cfg, workspace=tmp_path / "ws2",
                                      engine_factory=factory))
        assert result.passed is False
