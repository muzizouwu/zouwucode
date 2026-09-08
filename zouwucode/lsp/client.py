"""LSP (Language Server Protocol) client for diagnostics integration.

After each edit, the LSP client is called to check for diagnostics
and feed them into the LLM context for self-healing.
"""

import asyncio
import json
from pathlib import Path
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

        except Exception:
            pass
        return None