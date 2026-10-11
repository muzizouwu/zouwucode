"""Systematic regression: no failure path may ever surface a BLANK message.

The "task stuck / 不动了" incident had three faces of the same class of bug:
exceptions with empty str() (httpx timeouts), subprocesses failing without
output, and the engine aborting before feedback reached the model. These
tests lock in the structural guarantees:

1. errors.describe_exc never returns an empty/blank reason.
2. AgentCoordinator backfills ANY failed tool result with non-empty content.
3. verifiers annotate silent non-zero exits with the exit code.
"""

import asyncio

from zouwucode.agent.coordinator import AgentCoordinator
from zouwucode.config import ZOUWUCODEConfig
from zouwucode.engine.providers.base import ToolCall
from zouwucode.errors import describe_exc
from zouwucode.tools.base import BaseTool, ToolResult
from zouwucode.tools.registry import ToolRegistry


class TestDescribeExc:
    def test_empty_httpx_timeout_gets_reason(self):
        import httpx
        for exc in (httpx.ConnectTimeout(""), httpx.ReadTimeout(""),
                    httpx.ConnectError("")):
            text = describe_exc(exc)
            assert text.strip() and ":" in text
            assert len(text) > len(type(exc).__name__) + 2

    def test_asyncio_timeout_error(self):
        text = describe_exc(asyncio.TimeoutError())
        assert "超时" in text or "Timeout" in text

    def test_plain_exception_keeps_message(self):
        assert "detail here" in describe_exc(RuntimeError("detail here"))

    def test_never_blank(self):
        class Weird(Exception):
            def __str__(self):
                return "   "
        assert describe_exc(Weird()).strip() != ""


class _BlankFailTool(BaseTool):
    """A misbehaving tool: fails with NO output and NO error text."""

    def get_spec(self):
        from zouwucode.tools.base import ToolSpec
        return ToolSpec(name="blankfail", description="x",
                        parameters={}, required=[])

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(success=False, output="", error="")


class _EmptyExcFailTool(BaseTool):
    """Raises an exception whose str() is empty (httpx-style)."""

    def get_spec(self):
        from zouwucode.tools.base import ToolSpec
        return ToolSpec(name="emptyexc", description="x",
                        parameters={}, required=[])

    async def execute(self, **kwargs) -> ToolResult:
        import httpx
        raise httpx.ConnectTimeout("")


class TestCoordinatorBackfill:
    def _coord(self) -> AgentCoordinator:
        cfg = ZOUWUCODEConfig()
        registry = ToolRegistry()
        registry.register(_BlankFailTool())
        registry.register(_EmptyExcFailTool())
        return AgentCoordinator(cfg, engine=None, tool_registry=registry)

    def test_blank_failure_gets_reason(self):
        coord = self._coord()
        call = ToolCall(id="c1", name="blankfail", arguments="{}")
        res = asyncio.run(coord.execute_tool(call, context=None))
        assert res.is_error
        assert res.content.strip(), "error result must never be blank"
        assert "blankfail" in res.content

    def test_empty_str_exception_gets_reason(self):
        coord = self._coord()
        call = ToolCall(id="c2", name="emptyexc", arguments="{}")
        res = asyncio.run(coord.execute_tool(call, context=None))
        assert res.is_error
        assert res.content.strip()
        assert "ConnectTimeout" in res.content

    def test_error_in_error_field_promoted_to_content(self):
        """Tools that put the reason in .error (not .output) still inform."""
        class _ErrOnly(BaseTool):
            def get_spec(self):
                from zouwucode.tools.base import ToolSpec
                return ToolSpec(name="erronly", description="x",
                                parameters={}, required=[])

            async def execute(self, **kw):
                return ToolResult(success=False, output="",
                                  error="disk full")
        cfg = ZOUWUCODEConfig()
        registry = ToolRegistry()
        registry.register(_ErrOnly())
        coord = AgentCoordinator(cfg, engine=None, tool_registry=registry)
        res = asyncio.run(coord.execute_tool(
            ToolCall(id="c3", name="erronly", arguments="{}"), context=None))
        assert res.content == "disk full"


class TestVerifierSilentExit:
    def test_silent_nonzero_exit_annotated(self, tmp_path):
        from zouwucode.dev import verifiers
        # python exits 3 without printing anything (double quotes survive
        # both cmd.exe and POSIX shell -c quoting)
        ok, out = asyncio.run(verifiers._run_shell(
            'python -c "raise SystemExit(3)"', tmp_path))
        assert ok is False
        assert out.strip(), "silent failure must still carry a reason"
        assert "exit code 3" in out
