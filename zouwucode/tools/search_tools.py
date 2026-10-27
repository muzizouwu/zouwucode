"""Content search (grep) — find code by what it says, not just its filename.

Every mature coding agent exposes content search: ``glob`` answers "where is
this file", ``grep`` answers "where is this symbol/message". ZOUWUCODE had
only the former, while its own agent prompts told the model to "use grep" —
a tool that did not exist. This closes that gap.

Strategy: use ripgrep when it is on PATH (fast, respects .gitignore, skips
binaries), else fall back to a bounded pure-Python scan. Both paths share the
same skip list, output format and result cap, so behaviour is predictable.
"""

import asyncio
import logging
import os
import re
import shutil
from pathlib import Path

from .base import BaseTool, ToolResult, ToolSpec
from ..errors import describe_exc

logger = logging.getLogger("zouwucode.tools.search")

# Vendored deps / build output / VCS internals never carry the answer and can
# be enormous, so they are skipped by default in BOTH the rg and Python paths.
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".idea",
    ".vscode", "dist", "build", "zouwucode_data", ".zouwucode_worktrees",
    ".hello-my-zouwucode",
}

_MAX_FILE_BYTES = 2_000_000   # generated/binary blobs are not source code
_MAX_LINE_CHARS = 300         # one minified line must not flood the turn
_DEFAULT_MAX_RESULTS = 100
_MAX_RESULTS_CAP = 1000
_RG_TIMEOUT_SECONDS = 30.0


def _decode_text(data: bytes) -> str | None:
    """Decode a candidate text file, or None when it looks binary.

    UTF-16 files (common on Windows) are decoded too; NUL bytes elsewhere
    mean binary, which grep must not spray into the conversation.
    """
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    if b"\x00" in data[:4096]:
        return None
    return data.decode("utf-8", errors="replace")


def _shorten(line: str) -> str:
    line = line.rstrip("\r\n")
    if len(line) > _MAX_LINE_CHARS:
        return line[:_MAX_LINE_CHARS] + "…"
    return line


class GrepTool(BaseTool):
    """Search file CONTENTS with a regular expression."""

    def get_spec(self) -> ToolSpec:
        return ToolSpec(
            name="grep",
            description=(
                "Search file contents with a regular expression (like ripgrep). "
                "Use this to find where a symbol, string or error message lives "
                "— much faster and more precise than reading files one by one. "
                "Binary files, build output and VCS internals are skipped."
            ),
            parameters={
                "pattern": {
                    "type": "string",
                    "description": "Regular expression to search for (e.g. 'def main\\(')",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in (default: current directory)",
                },
                "include": {
                    "type": "string",
                    "description": "Only search files matching this glob (e.g. '*.py')",
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "Case-insensitive match (default: false)",
                },
                "files_only": {
                    "type": "boolean",
                    "description": "Return only the matching file paths, not the lines",
                },
                "max_results": {
                    "type": "integer",
                    "description": f"Maximum matches to return (default: {_DEFAULT_MAX_RESULTS})",
                },
            },
            required=["pattern"],
        )

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        include: str = "",
        ignore_case: bool = False,
        files_only: bool = False,
        max_results: int = _DEFAULT_MAX_RESULTS,
    ) -> ToolResult:
        if not pattern:
            return ToolResult(success=False, output="", error="pattern must not be empty")

        try:
            re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            return ToolResult(
                success=False, output="",
                error=f"Invalid regular expression: {exc}",
            )

        root = Path(path or ".").resolve()
        denied = await self._check_path(root)
        if denied:
            return ToolResult(success=False, output="", error=denied)
        if not root.exists():
            return ToolResult(success=False, output="", error=f"Path not found: {path}")

        limit = max(1, min(int(max_results or _DEFAULT_MAX_RESULTS), _MAX_RESULTS_CAP))
        rg = shutil.which("rg")
        if rg:
            matches, error = await self._search_ripgrep(
                rg, pattern, root, include, ignore_case, files_only, limit)
        else:
            matches, error = await asyncio.to_thread(
                self._search_python, pattern, root, include, ignore_case,
                files_only, limit)
        if error:
            # rg failing (e.g. unsupported flag on an old build) must not
            # surface as "no matches" — fall back to the Python scanner.
            logger.warning("ripgrep failed (%s); falling back to Python scan", error)
            matches, error = await asyncio.to_thread(
                self._search_python, pattern, root, include, ignore_case,
                files_only, limit)
            if error:
                return ToolResult(success=False, output="", error=error)

        if not matches:
            return ToolResult(
                success=True,
                output=f"No matches for pattern '{pattern}' in {root}",
            )

        body = "\n".join(matches)
        if len(matches) >= limit:
            body += (
                f"\n… (result limit reached: {limit} shown. Narrow the pattern, "
                "add an include filter, or search a subdirectory.)"
            )
        return ToolResult(success=True, output=body)

    async def _check_path(self, path: Path) -> str | None:
        checker = getattr(self.sandbox, "check_file_access", None) if self.sandbox else None
        if checker is None:
            return None
        if not await checker(str(path)):
            return f"Path outside allowed workspace: {path}"
        return None

    # ── ripgrep path ────────────────────────────────────────────────────────

    async def _search_ripgrep(
        self, rg: str, pattern: str, root: Path, include: str,
        ignore_case: bool, files_only: bool, limit: int,
    ) -> tuple[list[str], str | None]:
        args = [rg, "--no-heading", "--color=never", "--no-messages",
                "--max-columns", str(_MAX_LINE_CHARS),
                "--max-columns-preview", "--max-count", "20"]
        if ignore_case:
            args.append("--ignore-case")
        if files_only:
            args.append("--files-with-matches")
        else:
            args.append("--line-number")
        if include:
            args.extend(["--glob", include])
        # Searching a single file: run from its parent and target its name.
        cwd = root.parent if root.is_file() else root
        target = root.name if root.is_file() else "."
        # -e keeps a pattern starting with '-' from being read as a flag.
        args.extend(["-e", pattern, target])

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_RG_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return [], f"ripgrep timed out after {_RG_TIMEOUT_SECONDS:.0f}s"
        except Exception as exc:  # noqa: BLE001 — caller falls back
            return [], describe_exc(exc)

        # rg exit codes: 0 = matches, 1 = no matches, 2 = real error.
        if proc.returncode == 2:
            return [], stderr.decode("utf-8", errors="replace").strip() or "ripgrep error"

        lines = stdout.decode("utf-8", errors="replace").splitlines()
        out: list[str] = []
        for line in lines:
            rel = line[2:] if line.startswith("./") else line
            out.append(_shorten(rel))
            if len(out) >= limit:
                break
        return out, None

    # ── pure-Python fallback ────────────────────────────────────────────────

    def _search_python(
        self, pattern: str, root: Path, include: str,
        ignore_case: bool, files_only: bool, limit: int,
    ) -> tuple[list[str], str | None]:
        try:
            flags = re.IGNORECASE if ignore_case else 0
            regex = re.compile(pattern, flags)
        except re.error as exc:
            return [], f"Invalid regular expression: {exc}"

        if root.is_file():
            candidates = [root]
        else:
            candidates = self._walk(root)

        out: list[str] = []
        for file_path in candidates:
            if include and not self._matches_include(file_path, root, include):
                continue
            try:
                if file_path.stat().st_size > _MAX_FILE_BYTES:
                    continue
                data = file_path.read_bytes()
            except OSError:
                continue
            text = _decode_text(data)
            if text is None:
                continue
            rel = self._relative(file_path, root)
            file_hit = False
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    file_hit = True
                    if files_only:
                        break
                    out.append(_shorten(f"{rel}:{lineno}:{line}"))
                    if len(out) >= limit:
                        return out, None
            if files_only and file_hit:
                out.append(rel)
                if len(out) >= limit:
                    return out, None
        return out, None

    @staticmethod
    def _walk(root: Path):
        for dirpath, dirnames, filenames in os.walk(root):
            # Only the explicit skip list — hidden dirs like .github DO carry
            # source (workflows, templates) and must stay searchable.
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
            for name in sorted(filenames):
                yield Path(dirpath) / name

    @staticmethod
    def _matches_include(file_path: Path, root: Path, include: str) -> bool:
        rel = file_path.name if root.is_file() else str(
            file_path.relative_to(root)).replace(os.sep, "/")
        patterns = [p.strip() for p in include.split(",") if p.strip()]
        from fnmatch import fnmatch
        return any(fnmatch(rel, p) or fnmatch(file_path.name, p) for p in patterns)

    @staticmethod
    def _relative(file_path: Path, root: Path) -> str:
        if root.is_file():
            return root.name
        try:
            return str(file_path.relative_to(root)).replace(os.sep, "/")
        except ValueError:
            return str(file_path)
