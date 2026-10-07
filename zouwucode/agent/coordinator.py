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
            tool_result = ToolResult(
                tool_call_id=tool_call.id,
                content=result.output,
                is_error=not result.success,
            )
            # ── PostToolUse hooks (observe-only; never corrupt the result)
            await self.hooks.run_post(tool_call, tool_result)
            return tool_result
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=str(e),
                is_error=True,
            )
