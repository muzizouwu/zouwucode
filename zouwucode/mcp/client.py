"""MCP (Model Context Protocol) client — connect to external tool servers.

Two transports are wired:
- stdio: the server is spawned as a subprocess (``connect_stdio``)
- Streamable HTTP (2025-03-26): a single POST endpoint that answers with
  a plain JSON body OR a ``text/event-stream`` body; an Mcp-Session-Id
  handed out at initialize is echoed on every later request
  (``connect_http``).

External servers merge their tools into one registry under a prefix.
"""

import asyncio
import json
import subprocess
from typing import Optional

import httpx


class MCPClient:
    """Client for connecting to MCP (Model Context Protocol) servers.

    Transports: stdio (``connect_stdio``) and Streamable HTTP
    (``connect_http``, JSON or SSE response bodies, session-id echo).
    """

    def __init__(self):
        self._stdio_processes: dict[str, subprocess.Popen] = {}
        # name -> {"url", "headers", "session_id"} for Streamable HTTP servers
        self._http_servers: dict[str, dict] = {}
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

    # ── Streamable HTTP transport (2025-03-26) ──────────────────────────

    def _http_headers(self, info: dict) -> dict:
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        headers.update(info.get("headers") or {})
        if info.get("session_id"):
            headers["Mcp-Session-Id"] = info["session_id"]
        return headers

    @staticmethod
    def _parse_sse_body(text: str, want_id) -> dict:
        """Pull the matching JSON-RPC message out of an SSE response body."""
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue
            if want_id is None or msg.get("id") == want_id:
                if "error" in msg:
                    raise RuntimeError(f"MCP error: {msg['error']}")
                return msg
        raise RuntimeError(
            "MCP SSE response carried no JSON-RPC message with the "
            "expected id")

    async def _http_request(self, name: str, payload: dict,
                            timeout: float = 30.0) -> dict:
        """POST one JSON-RPC request to a Streamable-HTTP MCP server.

        Accepts both response flavours: a plain ``application/json`` body
        and the SSE variant (``text/event-stream``) carrying the same
        message. A 202/empty body returns ``{}`` (used for notifications).
        """
        info = self._http_servers[name]
        # trust_env=False: proxy environment variables must not silently
        # redirect local/dev MCP endpoints through a corporate proxy.
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.post(info["url"], json=payload,
                                     headers=self._http_headers(info))
        session = (resp.headers.get("Mcp-Session-Id")
                   or resp.headers.get("mcp-session-id"))
        if session:
            info["session_id"] = session
        if resp.status_code >= 400:
            raise RuntimeError(
                f"MCP HTTP {resp.status_code}: {resp.text[:200]}")
        ctype = resp.headers.get("Content-Type", "")
        if "text/event-stream" in ctype:
            return self._parse_sse_body(resp.text, payload.get("id"))
        text = (resp.text or "").strip()
        if not text:
            return {}
        msg = json.loads(text)
        if "error" in msg:
            raise RuntimeError(f"MCP error: {msg['error']}")
        return msg

    async def _http_notify(self, name: str, payload: dict) -> None:
        """Best-effort notification POST (no id, response ignored)."""
        info = self._http_servers[name]
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            await client.post(info["url"], json=payload,
                              headers=self._http_headers(info))

    async def connect_http(self, name: str, url: str,
                           headers: Optional[dict] = None) -> None:
        """Connect to a Streamable-HTTP MCP server and register its tools."""
        self._http_servers[name] = {"url": url,
                                    "headers": dict(headers or {}),
                                    "session_id": None}
        try:
            await self._http_request(name, {
                "jsonrpc": "2.0", "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "zouwucode", "version": "1.0.0"},
                },
            })
            # Spec: follow with notifications/initialized. Servers that do
            # not need it simply answer 202 — never fatal here.
            try:
                await self._http_notify(name, {
                    "jsonrpc": "2.0", "method": "notifications/initialized"})
            except Exception:
                pass
            tools_response = await self._http_request(name, {
                "jsonrpc": "2.0", "id": self._next_id(),
                "method": "tools/list", "params": {},
            })
        except Exception:
            self._http_servers.pop(name, None)
            raise
        for tool in tools_response.get("result", {}).get("tools", []):
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
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": original_name,
                "arguments": args,
            },
        }
        if server_name in self._http_servers:
            return await self._http_request(server_name, payload)
        proc = self._stdio_processes.get(server_name)

        if not proc:
            raise ValueError(f"MCP server not connected: {server_name}")

        return await self._request(proc, payload)

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
        """Disconnect from an MCP server and drop its registered tools."""
        proc = self._stdio_processes.pop(name, None)
        if proc:
            proc.terminate()
            await proc.wait()
        info = self._http_servers.pop(name, None)
        if info and info.get("session_id"):
            # Spec: best-effort session teardown via DELETE with the id.
            try:
                headers = {"Mcp-Session-Id": info["session_id"]}
                headers.update(info.get("headers") or {})
                async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
                    await client.delete(info["url"], headers=headers)
            except Exception:
                pass
        stale = [t for t, i in self._tools.items() if i["server"] == name]
        for tname in stale:
            self._tools.pop(tname, None)

    async def disconnect_all(self) -> None:
        """Disconnect from all MCP servers."""
        for name in list(set(self._stdio_processes) | set(self._http_servers)):
            await self.disconnect(name)