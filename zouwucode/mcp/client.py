"""MCP (Model Context Protocol) client — connect to external tool servers.

stdio transport is implemented and tested; SSE / Streamable HTTP are
reserved (declared in config, not wired yet). External servers merge their
tools into one registry under a prefix.
"""

import asyncio
import json
import subprocess
from typing import Optional

import httpx


class MCPClient:
    """Client for connecting to MCP (Model Context Protocol) servers.

    MCP servers can be:
    - stdio: spawned as a subprocess (e.g., `npx @modelcontextprotocol/server-filesystem`)
    - SSE: connects via Server-Sent Events
    - Streamable HTTP: connects via HTTP streaming
    """

    def __init__(self):
        self._stdio_processes: dict[str, subprocess.Popen] = {}
        self._sse_clients: dict[str, httpx.AsyncClient] = {}
        self._tools: dict[str, dict] = {}
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def _request(self, proc, payload: dict,
                       timeout: float = 30.0) -> dict:
        """Send one JSON-RPC request; read until the matching response id.

        Skips notifications/unrelated lines instead of assuming the very
        next line is our answer, and times out instead of hanging a session
        forever on a silent server.
        """
        proc.stdin.write((json.dumps(payload) + "\n").encode())
        await proc.stdin.drain()
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"MCP server did not answer '{payload.get('method')}' "
                    f"within {timeout:.0f}s")
            line = await asyncio.wait_for(proc.stdout.readline(),
                                          timeout=remaining)
            if not line:
                raise RuntimeError("MCP server closed the connection")
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                continue  # not JSON-RPC — ignore chatter
            if msg.get("id") != payload.get("id"):
                continue  # notification or stale response
            if "error" in msg:
                raise RuntimeError(f"MCP error: {msg['error']}")
            return msg

    async def connect_stdio(self, name: str, command: str, args: list[str]) -> None:
        """Connect to a stdio-based MCP server."""
        proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stdio_processes[name] = proc

        # Handshake
        await self._request(proc, {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "zouwucode", "version": "1.0.0"},
            },
        })

        # List tools
        tools_response = await self._request(proc, {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
            "params": {},
        })
        result = tools_response.get("result", {})
        tools = result.get("tools", [])

        for tool in tools:
            tool_name = f"{name}__{tool['name']}"
            self._tools[tool_name] = {
                "server": name,
                "original_name": tool["name"],
                "schema": tool,
            }

    async def call_tool(self, tool_name: str, args: dict) -> dict:
        """Call a tool on an MCP server."""
        tool_info = self._tools.get(tool_name)
        if not tool_info:
            raise ValueError(f"Unknown MCP tool: {tool_name}")

        server_name = tool_info["server"]
        original_name = tool_info["original_name"]
        proc = self._stdio_processes.get(server_name)

        if not proc:
            raise ValueError(f"MCP server not connected: {server_name}")

        return await self._request(proc, {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": original_name,
                "arguments": args,
            },
        })

    def get_tools(self) -> list[dict]:
        """Get all MCP tools as OpenAI-compatible tool definitions."""
        result = []
        for name, info in self._tools.items():
            schema = info["schema"]
            result.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": schema.get("description", ""),
                    "parameters": schema.get("inputSchema", {}),
                },
            })
        return result

    def iter_tools(self):
        """Iterate connected tools as (full_name, server, original_name, schema).

        Public accessor so extensions (e.g. McpExtension) don't need to
        touch the private ``_tools`` dict.
        """
        for name, info in self._tools.items():
            yield name, info["server"], info["original_name"], info["schema"]

    async def disconnect(self, name: str) -> None:
        """Disconnect from an MCP server."""
        proc = self._stdio_processes.get(name)
        if proc:
            proc.terminate()
            await proc.wait()
            del self._stdio_processes[name]

    async def disconnect_all(self) -> None:
        """Disconnect from all MCP servers."""
        for name in list(self._stdio_processes.keys()):
            await self.disconnect(name)