"""MCP extension — bridges MCP servers into the tool registry (reserved).

Inactive until ``extensions.mcp_servers`` is configured. When servers are
listed, ``start()`` connects each one through the existing
:class:`~zouwucode.mcp.client.MCPClient` (stdio transport) and wraps every
server tool as a :class:`MCPTool` BaseTool so it merges transparently into
the normal tool registry and sandbox/coordinator flow.
"""

import logging
from typing import Optional

from ..tools.base import BaseTool, ToolResult, ToolSpec
from .host import Extension, ExtensionContext

logger = logging.getLogger("zouwucode.extensions.mcp")


class MCPTool(BaseTool):
    """Adapter exposing one MCP server tool as a standard BaseTool."""

    def __init__(self, client, server: str, original_name: str, schema: dict):
        super().__init__(sandbox=None)  # sandbox handled by coordinator layer
        self._client = client
        self._server = server
        self._original_name = original_name
        self._schema = schema

    def get_spec(self) -> ToolSpec:
        return ToolSpec(
            name=f"{self._server}__{self._original_name}",
            description=self._schema.get("description", f"MCP tool {self._original_name}"),
            parameters=self._schema.get("inputSchema", {}).get("properties", {}),
            required=self._schema.get("inputSchema", {}).get("required", []),
        )

    async def execute(self, **kwargs) -> ToolResult:
        try:
            response = await self._client.call_tool(
                f"{self._server}__{self._original_name}", kwargs
            )
            # JSON-RPC style response: extract text content if present
            result = response.get("result", response)
            content = result.get("content", response)
            if isinstance(content, list):
                text = "\n".join(
                    item.get("text", str(item)) for item in content
                    if isinstance(item, dict)
                )
            else:
                text = str(content)
            return ToolResult(success=True, output=text)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output="", error=str(exc))


class McpExtension(Extension):
    """Connects configured MCP servers and contributes their tools."""

    name = "mcp"

    def __init__(self, server_configs: Optional[list] = None):
        # server_configs: list of McpServerConfig (name/command/args)
        self._configs = server_configs or []
        self._client = None
        self._tools: list[BaseTool] = []

    async def start(self, ctx: ExtensionContext) -> None:
        if not self._configs:
            logger.info("MCP extension: no servers configured — inactive.")
            return
        from ..mcp.client import MCPClient

        self._client = MCPClient()
        for server in self._configs:
            try:
                await self._client.connect_stdio(server.name, server.command, list(server.args))
                logger.info("MCP server connected: %s", server.name)
            except Exception as exc:  # noqa: BLE001
                logger.error("MCP server '%s' failed to connect: %s", server.name, exc)

        # Wrap every discovered server tool as a BaseTool.
        for _name, server, original_name, schema in self._client.iter_tools():
            self._tools.append(MCPTool(self._client, server, original_name, schema))
        logger.info("MCP extension contributed %d tool(s).", len(self._tools))

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.disconnect_all()
            self._client = None
        self._tools.clear()

    def get_tools(self) -> list[BaseTool]:
        return list(self._tools)
