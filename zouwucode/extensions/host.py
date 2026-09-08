"""Extension layer — reserved integration space for MCP / LSP and beyond.

Design
------
An :class:`Extension` is a lazily-started pluggable service that can
contribute two things to the agent runtime:

1. **Tools** — :class:`~zouwucode.tools.base.BaseTool` instances merged
   into the main ToolRegistry (e.g. MCP server tools, LSP diagnostics).
2. **Raw schemas** — OpenAI-format tool schemas with a matching async
   dispatcher, for tools that cannot be expressed as a BaseTool class
   (e.g. dynamically listed MCP server tools).

The :class:`ExtensionHost` owns the lifecycle (start/stop) and aggregation.
Everything is inactive until configured: ``extensions.mcp_servers`` empty
and ``extensions.lsp_enabled=false`` (the defaults) produce a no-op host,
so the reserved integration space costs nothing at runtime.

Future integrations only need to subclass Extension and register it —
no changes to the engine, coordinator or UI layers.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..tools.base import BaseTool, ToolResult, ToolSpec

logger = logging.getLogger("zouwucode.extensions")


@dataclass
class ExtensionContext:
    """Everything an extension may need from the host runtime."""

    config: object                                  # ZOUWUCODEConfig
    sandbox: object = None                          # PermissionManager
    tool_registry: object = None                    # ToolRegistry
    on_dynamic_tool: Optional[Callable] = None      # async (name, args) -> dict


class Extension(ABC):
    """Base class for pluggable runtime extensions (MCP / LSP / ...)."""

    name: str = "extension"

    async def start(self, ctx: ExtensionContext) -> None:
        """Acquire resources (spawn servers, open connections)."""

    async def stop(self) -> None:
        """Release resources. Must be safe to call twice."""

    def get_tools(self) -> list[BaseTool]:
        """Static tools contributed to the ToolRegistry."""
        return []

    def get_dynamic_schemas(self) -> list[dict]:
        """Raw OpenAI tool schemas dispatched via ctx.on_dynamic_tool."""
        return []


class ExtensionHost:
    """Aggregates extensions: lifecycle + tool/schema aggregation."""

    def __init__(self):
        self._extensions: dict[str, Extension] = {}
        self._started = False

    def register(self, extension: Extension) -> None:
        if self._started:
            raise RuntimeError("Cannot register extensions after start().")
        self._extensions[extension.name] = extension
        logger.info("Extension registered: %s", extension.name)

    def get(self, name: str) -> Optional[Extension]:
        return self._extensions.get(name)

    async def start(self, ctx: ExtensionContext) -> None:
        if self._started:
            return
        for ext in self._extensions.values():
            try:
                await ext.start(ctx)
                logger.info("Extension started: %s", ext.name)
            except Exception as exc:  # noqa: BLE001 — one bad extension must
                logger.error("Extension '%s' failed to start: %s", ext.name, exc)
                # not take down the whole agent
        self._started = True
        # Merge contributed tools into the registry. Callers must start the
        # host BEFORE freezing tool schemas (initialize_session / on_mount)
        # so extension tools are visible to the model.
        if ctx.tool_registry is not None:
            tools = self.get_tools()
            if tools:
                ctx.tool_registry.register_all(tools)
                logger.info("Registered %d extension tool(s).", len(tools))

    async def stop(self) -> None:
        for ext in self._extensions.values():
            try:
                await ext.stop()
            except Exception as exc:  # noqa: BLE001
                logger.error("Extension '%s' failed to stop: %s", ext.name, exc)
        self._started = False

    def get_tools(self) -> list[BaseTool]:
        tools: list[BaseTool] = []
        for ext in self._extensions.values():
            tools.extend(ext.get_tools())
        return tools

    def get_dynamic_schemas(self) -> list[dict]:
        schemas: list[dict] = []
        for ext in self._extensions.values():
            schemas.extend(ext.get_dynamic_schemas())
        return schemas

    def status_summary(self) -> list[dict]:
        return [
            {"name": ext.name, "tools": len(ext.get_tools()),
             "dynamic": len(ext.get_dynamic_schemas())}
            for ext in self._extensions.values()
        ]
