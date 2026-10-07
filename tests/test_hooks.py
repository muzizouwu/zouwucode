"""Tests for lifecycle hooks (PreToolUse blocking / PostToolUse observation)."""

import asyncio

from zouwucode.agent.hooks import HookRunner
from zouwucode.config import HookConfig, ZOUWUCODEConfig
from zouwucode.engine.providers.base import ToolCall, ToolResult


def tc(name="read", args='{"file_path": "a.py"}'):
    return ToolCall(id="c1", name=name, arguments=args)


class TestHookRunner:
    def test_no_hooks_is_noop(self):
        runner = HookRunner([])
        assert asyncio.run(runner.run_pre(tc())) is None
        asyncio.run(runner.run_post(tc(), ToolResult(
            tool_call_id="c1", content="x", is_error=False)))

    def test_pre_tool_exit_nonzero_blocks(self):
        hook = HookConfig(event="pre_tool", command="exit 3")
        runner = HookRunner([hook])
        reason = asyncio.run(runner.run_pre(tc()))
        assert reason is not None

    def test_pre_tool_json_block(self):
        # Write the emitting script to a temp file to dodge shell quoting.
        import tempfile, os
        script = ("import json\n"
                  "print(json.dumps({'block': True, 'reason': 'no touching prod'}))\n")
        fd, path = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        try:
            hook = HookConfig(event="pre_tool", command=f'python "{path}"')
            runner = HookRunner([hook])
            reason = asyncio.run(runner.run_pre(tc()))
            assert reason == "no touching prod"
        finally:
            os.unlink(path)

    def test_pre_tool_passes_when_exit_zero(self):
        hook = HookConfig(event="pre_tool", command="python -c pass")
        runner = HookRunner([hook])
        assert asyncio.run(runner.run_pre(tc())) is None

    def test_tool_pattern_filters(self):
        hook = HookConfig(event="pre_tool", command="exit 1",
                          tool_pattern="write")
        runner = HookRunner([hook])
        # read doesn't match → not blocked
        assert asyncio.run(runner.run_pre(tc("read"))) is None
        # write matches → blocked
        assert asyncio.run(runner.run_pre(tc("write"))) is not None

    def test_placeholder_substitution(self):
        marker = "hook_ran"
        py = f"print('{marker}')"
        hook = HookConfig(event="post_tool",
                          command=f'python -c "{py} {{tool}}"')
        runner = HookRunner([hook])
        # must not raise even though {tool} expands inside the command
        asyncio.run(runner.run_post(
            tc(), ToolResult(tool_call_id="c1", content="ok", is_error=False)))

    def test_coordinator_blocks_via_hook(self):
        """Integration: AgentCoordinator honors pre_tool veto."""
        from zouwucode.agent.coordinator import AgentCoordinator
        from zouwucode.tools.registry import ToolRegistry

        cfg = ZOUWUCODEConfig()
        cfg.extensions.hooks = [HookConfig(event="pre_tool",
                                           command="exit 7")]
        registry = ToolRegistry()
        coord = AgentCoordinator(cfg, engine=None, tool_registry=registry)
        result = asyncio.run(coord.execute_tool(tc(), context=None))
        assert result.is_error
        assert "Blocked by pre-tool hook" in result.content
