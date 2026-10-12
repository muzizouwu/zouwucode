"""MCP server mode — expose ZOUWUCODE's tools over MCP (stdio).

This is the inverse of the built-in MCP *client*: run
``zouwucode mcp-serve`` and any MCP client (Claude Code, Goose, Cursor, …)
can consume ZOUWUCODE's tools. Newline-delimited JSON-RPC 2.0 over stdio,
standard library only.

Protocol surface: initialize / tools/list / tools/call / ping, and the usual
notifications. Tool schemas are advertised from the same registry the agent
uses, so the two stay in lockstep.
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path

from .. import __version__
from ..config import ZOUWUCODEConfig
from ..runtime import create_builtin_tools
from ..sandbox.permission import PermissionManager
from ..tools.registry import ToolRegistry

PROTOCOL_FALLBACK = "2025-06-18"
SERVER_NAME = "zouwucode"


def build_registry() -> ToolRegistry:
    """Build the standard toolset rooted at the current workspace."""
    config = ZOUWUCODEConfig.load()
    sandbox = PermissionManager(config.sandbox)
    sandbox.set_workspace(Path.cwd())
    registry = ToolRegistry()
    registry.register_all(create_builtin_tools(sandbox, config))
    return registry


class McpServer:
    """Minimal MCP tool server over stdio."""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    # ── output ──────────────────────────────────────────────────────────────
    @staticmethod
    def _write(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def _result(self, mid, result) -> None:
        self._write({"jsonrpc": "2.0", "id": mid, "result": result})

    def _error(self, mid, code, message) -> None:
        self._write({"jsonrpc": "2.0", "id": mid,
                     "error": {"code": code, "message": message}})

    # ── tools ───────────────────────────────────────────────────────────────
    def _tool_list(self):
        out = []
        for schema in self.registry.get_schemas():
            fn = schema.get("function", {})
            out.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "inputSchema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return out

    def _call_tool(self, name: str, args: dict):
        try:
            result = asyncio.run(self.registry.execute(name, **(args or {})))
        except TypeError as exc:
            return {"content": [{"type": "text", "text": f"Bad arguments: {exc}"}],
                    "isError": True}
        except Exception as exc:  # noqa: BLE001
            return {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                    "isError": True}
        ok = getattr(result, "success", True)
        text = (getattr(result, "output", "") or "") if ok else \
            (getattr(result, "error", "") or getattr(result, "output", "") or "tool failed")
        return {"content": [{"type": "text", "text": text}], "isError": (not ok)}

    # ── dispatch ────────────────────────────────────────────────────────────
    def handle(self, msg: dict) -> None:
        method = msg.get("method")
        mid = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            self._result(mid, {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_FALLBACK,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
                "instructions": ("ZOUWUCODE tools (read/write/edit/bash/git/web/"
                                 "python_exec/repo_map/...). Workspace-scoped."),
            })
            return
        if method in ("notifications/initialized", "initialized",
                      "notifications/cancelled", "logging/setLevel",
                      "notifications/roots/list_changed"):
            return
        if method == "ping":
            self._result(mid, {})
            return
        if method == "tools/list":
            self._result(mid, {"tools": self._tool_list()})
            return
        if method in ("resources/list", "prompts/list"):
            self._result(mid, {"resources": []} if method == "resources/list" else {"prompts": []})
            return
        if method == "tools/call":
            name = params.get("name", "")
            if name not in self.registry.get_names():
                self._error(mid, -32602, f"Unknown tool: {name}")
                return
            self._result(mid, self._call_tool(name, params.get("arguments") or {}))
            return
        if mid is None:
            return  # unknown notification
        self._error(mid, -32601, f"Unsupported method: {method}")

    def serve_forever(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                print(f"[zouwucode-mcp] bad JSON line ignored: {line[:120]}", file=sys.stderr)
                continue
            try:
                if isinstance(msg, list):
                    for m in msg:
                        self.handle(m)
                else:
                    self.handle(msg)
            except Exception:  # noqa: BLE001
                print("[zouwucode-mcp] uncaught:\n" + traceback.format_exc(), file=sys.stderr)


def main(argv=None) -> int:
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    if argv and ("--help" in argv or "-h" in argv):
        print(__doc__)
        return 0
    registry = build_registry()
    print(f"[zouwucode-mcp] serving {len(registry.get_names())} tools "
          f"| workspace={Path.cwd()}", file=sys.stderr)
    McpServer(registry).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
