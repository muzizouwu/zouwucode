"""Regression: the agent-mode tool-approval gate (y/n/a) actually gates.

Defects pinned:
- The system prompt once claimed agent mode required approval while the
  agent/yolo code paths were byte-identical (no approval code existed).
- Now: AgentCoordinator holds approval_callback; interactive frontends
  (CLI/TUI/Web) install one, headless paths (dev/eval/single-message) and
  sub-agents leave it None or run in yolo — no prompt storms, no hangs.
"""

import asyncio
import inspect

from zouwucode.agent.coordinator import AgentCoordinator
from zouwucode.config import ZOUWUCODEConfig
from zouwucode.engine.providers.base import ToolCall
from zouwucode.tools.base import ToolSpec, ToolResult
from zouwucode.tools.registry import ToolRegistry


class _EchoTool:
    def get_name(self):
        return "echo"

    def get_spec(self):
        return ToolSpec(
            name="echo",
            description="echo text back",
            parameters={"text": {"type": "string"}},
            required=["text"],
        )

    async def execute(self, text=""):
        return ToolResult(success=True, output=f"echo:{text}")


class _Ctx:
    """Minimal TurnContext stand-in (only .mode is read by the gate)."""

    def __init__(self, mode):
        self.mode = mode


def _coord():
    registry = ToolRegistry()
    registry.register(_EchoTool())
    return AgentCoordinator(ZOUWUCODEConfig(), None, registry)


def _call():
    return ToolCall(id="t1", name="echo", arguments='{"text": "hi"}')


class TestApprovalGate:
    def test_no_callback_executes_normally(self):
        coord = _coord()
        r = asyncio.run(coord.execute_tool(_call(), _Ctx("agent")))
        assert not r.is_error and "echo:hi" in r.content

    def test_denied_call_is_error_and_never_runs(self):
        coord = _coord()

        async def deny(tool_call, context):
            return False

        coord.approval_callback = deny
        r = asyncio.run(coord.execute_tool(_call(), _Ctx("agent")))
        assert r.is_error and "DENIED" in r.content

    def test_approved_call_runs(self):
        coord = _coord()

        async def allow(tool_call, context):
            return True

        coord.approval_callback = allow
        r = asyncio.run(coord.execute_tool(_call(), _Ctx("agent")))
        assert not r.is_error and "echo:hi" in r.content

    def test_yolo_mode_never_invokes_the_callback(self):
        coord = _coord()
        seen = []

        async def cb(tool_call, context):
            seen.append(tool_call.name)
            return False

        coord.approval_callback = cb
        r = asyncio.run(coord.execute_tool(_call(), _Ctx("yolo")))
        assert not r.is_error and seen == []

    def test_broken_callback_denies_instead_of_wedging(self):
        coord = _coord()

        async def boom(tool_call, context):
            raise RuntimeError("ui exploded")

        coord.approval_callback = boom
        r = asyncio.run(coord.execute_tool(_call(), _Ctx("agent")))
        assert r.is_error and "NOT executed" in r.content


class TestFrontendWiring:
    def test_cli_and_tui_install_the_gate(self):
        from zouwucode.tui import app as cli_mod
        from zouwucode.tui import textual_app as tui_mod
        assert "approval_callback" in inspect.getsource(
            cli_mod.ZOUWUCODEApp.run_interactive)
        assert "approval_callback" in inspect.getsource(tui_mod.ZOUWUCODETUI.__init__)
        assert "ApprovalScreen" in inspect.getsource(tui_mod)

    def test_subagents_never_prompt(self):
        from zouwucode.agent import subagent as sub_mod
        src = inspect.getsource(sub_mod.SubAgent.__init__)
        assert 'set_mode("yolo")' in src

    def test_webui_has_approval_channel(self):
        from zouwucode.webui import server as srv
        src = inspect.getsource(srv)
        assert "/api/approve" in src
        assert "approval_request" in src
        assert "approval_callback" in src

    def test_prompts_describe_the_real_gate(self):
        from zouwucode.tui import app as cli_mod
        from zouwucode.tui import textual_app as tui_mod
        for src in (inspect.getsource(cli_mod.ZOUWUCODEApp._build_system_prompt),
                    inspect.getsource(tui_mod.ZOUWUCODETUI._build_system_prompt)):
            assert "no interactive approval" not in src
            assert "approval" in src
