"""LSP (Language Server Protocol) client for diagnostics integration.

After each edit, the LSP client is called to check for diagnostics
and feed them into the LLM context for self-healing.
"""
import logging

logger = logging.getLogger("zouwucode.lsp.client")


import asyncio
import json
import time
from pathlib import Path
from urllib.parse import unquote, urlparse
from typing import Optional


# Map file extensions to LSP servers
LSP_SERVERS = {
    ".py": ("pyright-langserver", ["--stdio"]),
    ".js": ("typescript-language-server", ["--stdio"]),
    ".ts": ("typescript-language-server", ["--stdio"]),
    ".tsx": ("typescript-language-server", ["--stdio"]),
    ".jsx": ("typescript-language-server", ["--stdio"]),
    ".rs": ("rust-analyzer", []),
    ".go": ("gopls", []),
    ".java": ("eclipse-jdtls", []),
    ".cpp": ("clangd", []),
    ".c": ("clangd", []),
    ".h": ("clangd", []),
}


class LSPClient:
    """Lightweight LSP client for fetching diagnostics after edits.

    Uses a simple approach: spawns the language server, sends
    didOpen/didChange, and collects diagnostics.
    """

    def __init__(self):
        self._request_id = 1

    def get_lsp_command(self, file_path: str) -> Optional[list[str]]:
        """Get the LSP server command for a file type."""
        ext = Path(file_path).suffix.lower()
        info = LSP_SERVERS.get(ext)
        if info:
            return [info[0]] + info[1]
        return None

    async def get_diagnostics(self, file_path: str, content: str) -> list[dict]:
        """Get LSP diagnostics for a file.

        Returns a list of diagnostic dicts with severity, message, and range.
        """
        ext = Path(file_path).suffix.lower()
        lang_id = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "typescriptreact",
            ".jsx": "javascriptreact",
            ".rs": "rust",
            ".go": "go",
            ".java": "java",
            ".cpp": "cpp",
            ".c": "c",
            ".h": "c",
        }.get(ext, "")

        if not lang_id:
            return []

        lsp_cmd = self.get_lsp_command(file_path)
        if not lsp_cmd:
            return []

        try:
            proc = await asyncio.create_subprocess_exec(
                *lsp_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Initialize
            await self._send(proc, {
                "jsonrpc": "2.0",
                "id": self._get_id(),
                "method": "initialize",
                "params": {
                    "processId": None,
                    "capabilities": {},
                    "rootUri": None,
                },
            })
            await self._recv(proc)

            # Initialized notification
            await self._send(proc, {
                "jsonrpc": "2.0",
                "method": "initialized",
                "params": {},
            })

            # Open document
            uri = Path(file_path).as_uri()
            await self._send(proc, {
                "jsonrpc": "2.0",
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": uri,
                        "languageId": lang_id,
                        "version": 1,
                        "text": content,
                    },
                },
            })

            # Wait a bit for diagnostics
            await asyncio.sleep(0.5)

            # Collect diagnostics from notifications
            diagnostics = []
            try:
                while True:
                    line = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=0.5
                    )
                    if not line:
                        break
                    msg = json.loads(line.decode())
                    method = msg.get("method", "")
                    if method == "textDocument/publishDiagnostics":
                        params = msg.get("params", {})
                        for diag in params.get("diagnostics", []):
                            diagnostics.append({
                                "file": file_path,
                                "message": diag.get("message", ""),
                                "severity": diag.get("severity", 0),
                                "range": diag.get("range", {}),
                            })
            except (asyncio.TimeoutError, json.JSONDecodeError):
                pass

            proc.terminate()
            return diagnostics

        except Exception:
            return []

    def _get_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send(self, proc: asyncio.subprocess.Process, msg: dict) -> None:
        content = json.dumps(msg) + "\n"
        content_length = len(content.encode())
        header = f"Content-Length: {content_length}\r\n\r\n"
        proc.stdin.write(header.encode() + content.encode())
        await proc.stdin.drain()

    async def _recv(self, proc: asyncio.subprocess.Process) -> Optional[dict]:
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
            if not line:
                return None

            # Parse LSP headers
            header_str = line.decode()
            while line.strip():
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)

            # Read content
            content_length = 0
            for header in header_str.split("\r\n"):
                if header.lower().startswith("content-length:"):
                    content_length = int(header.split(":")[1].strip())

            if content_length > 0:
                content = await asyncio.wait_for(
                    proc.stdout.readexactly(content_length), timeout=5.0
                )
                return json.loads(content.decode())

        except Exception as _exc:
            logger.debug("swallowed %s: %s", type(_exc).__name__, _exc)
        return None


    # ── Hover / definition (richer code intelligence than diagnostics) ──────
    # The original client only fetched diagnostics; hover (what is this symbol)
    # and go-to-definition (where is it declared) are the two queries an agent
    # needs most when navigating unfamiliar code.

    _LANG_ID = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".tsx": "typescriptreact", ".jsx": "javascriptreact", ".rs": "rust",
        ".go": "go", ".java": "java", ".cpp": "cpp", ".c": "c", ".h": "c",
    }

    def _lang_id(self, file_path: str) -> str:
        return self._LANG_ID.get(Path(file_path).suffix.lower(), "")

    @staticmethod
    def _uri_to_path(uri: str) -> str:
        """file:///C:/x/y.py -> C:/x/y.py (best effort, cross-platform)."""
        try:
            parsed = urlparse(uri)
            path = unquote(parsed.path or "")
            if parsed.netloc and parsed.netloc not in ("", "localhost"):
                path = "//" + parsed.netloc + path
            # Windows: leading slash before a drive letter must go
            if len(path) > 2 and path[0] == "/" and path[2] == ":":
                path = path[1:]
            return path
        except Exception:  # noqa: BLE001
            return uri

    @staticmethod
    def _format_hover(result) -> str:
        """Normalise an LSP Hover result to plain text."""
        if not result:
            return ""
        contents = result.get("contents") if isinstance(result, dict) else result
        if isinstance(contents, str):
            return contents
        if isinstance(contents, dict):
            return contents.get("value", "") or ""
        if isinstance(contents, list):
            parts = []
            for c in contents:
                if isinstance(c, str):
                    parts.append(c)
                elif isinstance(c, dict):
                    parts.append(c.get("value", "") or "")
            return "\n".join(x for x in parts if x)
        return ""

    async def _recv_msg(self, proc, timeout: float):
        """Read exactly one Content-Length-framed LSP message (or None)."""
        try:
            header = b""
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
                if not line:
                    return None
                header += line
                if line in (b"\r\n", b"\n"):
                    break
            length = 0
            for h in header.decode(errors="replace").splitlines():
                if h.lower().startswith("content-length:"):
                    length = int(h.split(":", 1)[1].strip())
            if length <= 0:
                return None
            body = await asyncio.wait_for(proc.stdout.readexactly(length), timeout=timeout)
            return json.loads(body.decode(errors="replace"))
        except Exception as _exc:
            logger.debug("swallowed %s: %s", type(_exc).__name__, _exc)
            return None

    async def _request(self, proc, method: str, params: dict, timeout: float = 5.0):
        """Send a request and wait for the response with the matching id."""
        rid = self._get_id()
        await self._send(proc, {"jsonrpc": "2.0", "id": rid,
                                "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            msg = await self._recv_msg(proc, remaining)
            if msg is None:
                return None
            if msg.get("id") == rid:
                return msg.get("result")
        return None

    def _read_or(self, file_path: str, content: str) -> str:
        if content:
            return content
        try:
            return Path(file_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    async def _start(self, file_path: str, content: str):
        """Spawn + initialize a language server with the file open. None on failure."""
        lsp_cmd = self.get_lsp_command(file_path)
        lang = self._lang_id(file_path)
        if not lsp_cmd or not lang:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                *lsp_cmd, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except Exception as _exc:  # server not installed etc.
            logger.debug("swallowed %s: %s", type(_exc).__name__, _exc)
            return None
        await self._send(proc, {"jsonrpc": "2.0", "id": self._get_id(),
                                "method": "initialize",
                                "params": {"processId": None, "capabilities": {},
                                           "rootUri": None}})
        await self._recv(proc)
        await self._send(proc, {"jsonrpc": "2.0", "method": "initialized",
                                "params": {}})
        await self._send(proc, {"jsonrpc": "2.0", "method": "textDocument/didOpen",
                                "params": {"textDocument": {
                                    "uri": Path(file_path).as_uri(),
                                    "languageId": lang, "version": 1,
                                    "text": self._read_or(file_path, content)}}})
        await asyncio.sleep(0.2)
        return proc

    async def hover(self, file_path: str, line: int, character: int,
                    content: str = "") -> str:
        """Return the hover text for a position (0-based line/character)."""
        proc = await self._start(file_path, content)
        if proc is None:
            return ""
        try:
            result = await self._request(proc, "textDocument/hover", {
                "textDocument": {"uri": Path(file_path).as_uri()},
                "position": {"line": int(line), "character": int(character)}})
            return self._format_hover(result)
        finally:
            try:
                proc.terminate()
            except Exception as _exc:
                logger.debug("swallowed %s: %s", type(_exc).__name__, _exc)
        return ""

    async def definition(self, file_path: str, line: int, character: int,
                         content: str = "") -> list:
        """Return definitions for a position as [{file,line,character}, ...]."""
        proc = await self._start(file_path, content)
        if proc is None:
            return []
        try:
            result = await self._request(proc, "textDocument/definition", {
                "textDocument": {"uri": Path(file_path).as_uri()},
                "position": {"line": int(line), "character": int(character)}})
        finally:
            try:
                proc.terminate()
            except Exception as _exc:
                logger.debug("swallowed %s: %s", type(_exc).__name__, _exc)
        if not result:
            return []
        if isinstance(result, dict):
            result = [result]
        out = []
        for loc in result:
            if not isinstance(loc, dict):
                continue
            start = loc.get("range", {}).get("start", {})
            out.append({
                "file": self._uri_to_path(loc.get("uri", "")),
                "line": start.get("line", 0),
                "character": start.get("character", 0),
            })
        return out
