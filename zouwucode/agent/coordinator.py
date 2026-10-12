"""Agent coordinator — executes tool calls requested by the engine loop.

The coordinator is the single callback the EngineLoop uses to turn a
ToolCall into a ToolResult: it parses the JSON-string arguments, runs
lifecycle hooks (PreToolUse may block; PostToolUse observes), then
dispatches to the ToolRegistry.
"""

import json

from ..engine.loop import EngineLoop, TurnContext
from ..engine.providers.base import ToolCall, ToolResult
from ..tools.registry import ToolRegistry
from ..config import ZOUWUCODEConfig
from ..errors import describe_exc
from .hooks import HookRunner


class AgentCoordinator:
    """Executes engine tool calls through the tool registry."""

    def __init__(
        self,
        config: ZOUWUCODEConfig,
        engine: EngineLoop,
        tool_registry: ToolRegistry,
    ):
        self.config = config
        self.engine = engine
        self.tools = tool_registry
        self.hooks = HookRunner(getattr(config.extensions, "hooks", []))
        # Optional interactive approval (agent mode only). Frontends that can
        # ask a human (CLI/TUI/Web) install an async callback returning True
        # to allow; headless paths (dev/eval/single-message) leave it None so
        # behavior stays byte-identical to before the gate existed.
        self.approval_callback = None

    async def execute_tool(self, tool_call: ToolCall, context: TurnContext) -> ToolResult:
        """Execute a tool call with permission checking.

        This is the main callback used by the engine loop.
        """
        # Execute the tool
        try:
            # ── PreToolUse hooks: a non-zero exit or {"block": true} vetoes
            # the call; the reason goes back to the model as an error result.
            block_reason = await self.hooks.run_pre(tool_call)
            if block_reason:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=f"Blocked by pre-tool hook: {block_reason}",
                    is_error=True,
                )
            # ── Interactive approval (agent mode only; None elsewhere) ────
            # A denied call comes back as a normal error result, so the model
            # SEES the refusal and can adapt instead of silently proceeding.
            if (self.approval_callback is not None
                    and getattr(context, "mode", None) == "agent"):
                try:
                    approved = await self.approval_callback(tool_call, context)
                except Exception as exc:  # a broken prompt must not wedge the loop
                    return ToolResult(
                        tool_call_id=tool_call.id,
                        content=(f"Approval prompt failed ({type(exc).__name__}: {exc}) "
                                 f"— '{tool_call.name}' was NOT executed."),
                        is_error=True,
                    )
                if not approved:
                    return ToolResult(
                        tool_call_id=tool_call.id,
                        content=(f"User DENIED tool '{tool_call.name}'. It was not "
                                 "executed. Do not retry it as-is — ask the user what "
                                 "they want, or choose a safer alternative."),
                        is_error=True,
                    )
            # tool_call.arguments is a JSON string from the stream — parse it
            # before unpacking with ** (TypeError otherwise)
            args = tool_call.arguments
            if isinstance(args, str):
                args = json.loads(args) if args.strip() else {}
            if not isinstance(args, dict):
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=f"Tool arguments must be a JSON object, got: {tool_call.arguments!r}",
                    is_error=True,
                )
            result = await self.tools.execute(tool_call.name, **args)
            content = result.output or ""
            if not result.success:
                # An error result must ALWAYS carry a reason, and keep BOTH
                # streams: stdout may hold partial useful output while .error
                # holds the real reason. Previously the error was dropped
                # whenever output was non-empty, leaving the model (and the
                # user) blind to why the tool failed.
                parts = [pp for pp in (content.strip(),
                                       (result.error or "").strip()) if pp]
                content = "\n".join(parts) or \
                    f"Tool '{tool_call.name}' failed with no error message"
            tool_result = ToolResult(
                tool_call_id=tool_call.id,
                content=content,
                is_error=not result.success,
            )
            # ── PostToolUse hooks (observe-only; never corrupt the result)
            await self.hooks.run_post(tool_call, tool_result)
            return tool_result
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=describe_exc(e),
                is_error=True,
            )
