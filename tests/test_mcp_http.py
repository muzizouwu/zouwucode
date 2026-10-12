"""Regression: MCP Streamable HTTP transport (2025-03-26).

Defect pinned: the client was stdio-only; SSE / Streamable HTTP were
"reserved" in config but nothing was wired. Servers are spoken to over a
single POST endpoint that may answer with a plain JSON body OR a
``text/event-stream`` body, and may hand out an Mcp-Session-Id that must
be echoed on every subsequent request.
"""

import asyncio
import inspect
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from zouwucode.mcp.client import MCPClient


class _FakeMCPHandler(BaseHTTPRequestHandler):
    server_version = "fake-mcp"

    def log_message(self, *args):  # keep test output clean
        pass

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}

    def _reply(self, msg, session=False):
        body = json.dumps(msg).encode("utf-8")
        self.server.seen_session_headers.append(
            self.headers.get("Mcp-Session-Id"))
        if self.server.mode == "sse":
            payload = b"event: message\ndata: " + body + b"\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
        else:
            payload = body
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        if session:
            self.send_header("Mcp-Session-Id", "sess-abc123")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        msg = self._read_json()
        method = msg.get("method")
        if method == "initialize":
            self._reply({"jsonrpc": "2.0", "id": msg.get("id"),
                         "result": {"protocolVersion": "2025-03-26",
                                    "capabilities": {},
                                    "serverInfo": {"name": "fake",
                                                   "version": "0"}}},
                        session=True)
        elif method == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif method == "tools/list":
            self._reply({"jsonrpc": "2.0", "id": msg.get("id"),
                         "result": {"tools": [{
                             "name": "echo",
                             "description": "Echo text back",
                             "inputSchema": {"type": "object",
                                             "properties": {"text": {"type": "string"}}},
                         }]}})
        elif method == "tools/call":
            args = (msg.get("params") or {}).get("arguments") or {}
            self._reply({"jsonrpc": "2.0", "id": msg.get("id"),
                         "result": {"content": [
                             {"type": "text",
                              "text": "echo:" + str(args.get("text", ""))}]}})
        else:
            self._reply({"jsonrpc": "2.0", "id": msg.get("id"),
                         "error": {"code": -32601,
                                   "message": "method not found"}})

    def do_DELETE(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture
def mcp_http_server():
    servers = []

    def _start(mode="json"):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeMCPHandler)
        httpd.mode = mode
        httpd.seen_session_headers = []
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        servers.append(httpd)
        host, port = httpd.server_address[:2]
        return f"http://{host}:{port}/mcp", httpd

    yield _start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def _run(coro):
    return asyncio.run(coro)


class TestHttpTransport:
    def test_initialize_lists_tools_and_calls(self, mcp_http_server):
        url, _ = mcp_http_server("json")

        async def scenario():
            client = MCPClient()
            await client.connect_http("fake", url)
            names = [t["function"]["name"] for t in client.get_tools()]
            result = await client.call_tool("fake__echo", {"text": "hi"})
            await client.disconnect_all()
            return names, result

        names, result = _run(scenario())
        assert "fake__echo" in names
        assert result["result"]["content"][0]["text"] == "echo:hi"

    def test_sse_response_body(self, mcp_http_server):
        url, _ = mcp_http_server("sse")

        async def scenario():
            client = MCPClient()
            await client.connect_http("fake", url)
            result = await client.call_tool("fake__echo", {"text": "sse"})
            await client.disconnect_all()
            return result

        result = _run(scenario())
        assert result["result"]["content"][0]["text"] == "echo:sse"

    def test_session_id_is_echoed_after_initialize(self, mcp_http_server):
        url, httpd = mcp_http_server("json")

        async def scenario():
            client = MCPClient()
            await client.connect_http("fake", url)
            await client.disconnect_all()

        _run(scenario())
        # initialize itself has no session yet; tools/list and DELETE must echo it.
        assert httpd.seen_session_headers[0] is None
        assert "sess-abc123" in httpd.seen_session_headers[1:]

    def test_disconnect_drops_tools(self, mcp_http_server):
        url, _ = mcp_http_server("json")

        async def scenario():
            client = MCPClient()
            await client.connect_http("fake", url)
            await client.disconnect("fake")
            return client.get_tools()

        assert _run(scenario()) == []

    def test_unknown_tool_raises(self, mcp_http_server):
        url, _ = mcp_http_server("json")

        async def scenario():
            client = MCPClient()
            await client.connect_http("fake", url)
            try:
                with pytest.raises(ValueError):
                    await client.call_tool("fake__nope", {})
            finally:
                await client.disconnect_all()

        _run(scenario())

    def test_error_response_raises(self, mcp_http_server):
        url, _ = mcp_http_server("json")

        async def scenario():
            client = MCPClient()
            await client.connect_http("fake", url)
            try:
                with pytest.raises(RuntimeError) as exc:
                    await client._http_request(
                        "fake", {"jsonrpc": "2.0", "id": 999,
                                 "method": "no/such"})
                return str(exc.value)
            finally:
                await client.disconnect_all()

        assert "method not found" in _run(scenario())


class TestExtensionRouting:
    def test_extension_prefers_url_transport(self):
        from zouwucode.extensions import mcp_ext
        src = inspect.getsource(mcp_ext.McpExtension.start)
        assert "connect_http" in src and "url" in src

    def test_config_accepts_url_and_headers(self):
        from zouwucode.config import McpServerConfig
        cfg = McpServerConfig(name="remote", url="http://x/mcp",
                              headers={"Authorization": "Bearer t"})
        assert cfg.url and cfg.command == ""

    def test_stdio_config_still_works(self):
        from zouwucode.config import McpServerConfig
        cfg = McpServerConfig(name="fs", command="npx", args=["-y", "x"])
        assert cfg.command == "npx" and cfg.url == ""
