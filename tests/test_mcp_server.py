"""Regression for v1.1.0: MCP server mode (expose tools over stdio).

Spawns `python -m zouwucode mcp-serve`, speaks JSON-RPC to it, and checks a
real tool call round-trips. This is the inverse of the MCP client.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _run_mcp(requests, cwd, timeout=90):
    payload = "".join(json.dumps(r) + "\n" for r in requests)
    proc = subprocess.run(
        [sys.executable, "-m", "zouwucode", "mcp-serve"],
        input=payload, capture_output=True, text=True, cwd=str(cwd), timeout=timeout,
    )
    out = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" in obj:
            out[obj["id"]] = obj
    return out, proc


class TestMcpServer:
    def test_initialize_and_tools_list(self, tmp_path):
        res, proc = _run_mcp([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ], cwd=ROOT)
        assert 1 in res and res[1]["result"]["serverInfo"]["name"] == "zouwucode"
        tools = {t["name"] for t in res[2]["result"]["tools"]}
        # core tools advertised with schemas
        for expected in ("read", "write", "edit", "bash", "git", "repo_map"):
            assert expected in tools, expected
        sample = [t for t in res[2]["result"]["tools"] if t["name"] == "read"][0]
        assert sample["inputSchema"].get("type") == "object"

    def test_tools_call_round_trips(self, tmp_path):
        (tmp_path / "hello.py").write_text("def hi():\n    return 1\n", encoding="utf-8")
        res, proc = _run_mcp([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "repo_map", "arguments": {"path": "."}}},
        ], cwd=tmp_path)
        assert 2 in res
        content = res[2]["result"]["content"][0]["text"]
        assert "hello.py" in content and res[2]["result"]["isError"] is False

    def test_unknown_tool_is_error(self):
        res, proc = _run_mcp([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "not_a_tool", "arguments": {}}},
        ], cwd=ROOT)
        assert "error" in res[2]

    def test_ping(self):
        res, proc = _run_mcp([
            {"jsonrpc": "2.0", "id": 7, "method": "ping"},
        ], cwd=ROOT)
        assert res[7]["result"] == {}

    def test_subcommand_registered(self):
        src = (ROOT / "zouwucode" / "__main__.py").read_text(encoding="utf-8")
        assert 'sys.argv[1] == "mcp-serve"' in src
