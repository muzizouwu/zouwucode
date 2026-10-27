"""File operation tools — read, write, edit, list, glob."""

import difflib
from pathlib import Path
from typing import Optional

from .base import BaseTool, ToolSpec, ToolResult
from ..errors import describe_exc

# Tool output is fed back to the model and rendered in every frontend, so a
# huge diff must not flood the turn. 4k chars covers typical edits comfortably.
_MAX_DIFF_CHARS = 4000
_MAX_NEW_FILE_LINES = 40

# Reads are capped like every mature coding agent caps them: one unbounded
# read of a generated/minified file can blow the whole context window and
# stall the task with a pointless compaction. The model can page with
# offset/limit instead.
_MAX_READ_LINES = 2000
_MAX_READ_CHARS = 100_000
_MAX_READ_FILE_BYTES = 10 * 1024 * 1024

# Safety net for `glob`: a huge tree must not answer with 10k paths.
_MAX_GLOB_RESULTS = 200


def _unified_diff(old: str, new: str, path: str,
                  context: int = 3) -> str:
    """Return a truncated unified diff between two texts.

    Line endings are normalised first: a pure CRLF/LF difference is not a
    meaningful change and used to render as "every line changed".
    """
    lines = difflib.unified_diff(
        _normalize_nl(old).splitlines(keepends=True),
        _normalize_nl(new).splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=context,
    )
    text = "".join(lines).rstrip("\n")
    if not text:
        return "(no textual change)"
    if len(text) > _MAX_DIFF_CHARS:
        return (text[:_MAX_DIFF_CHARS]
                + f"\n… (diff truncated — {len(text)} chars total)")
    return text


def _new_file_preview(content: str) -> str:
    """Preview of a newly created file (kept short to protect the context)."""
    lines = content.splitlines()
    shown = lines[:_MAX_NEW_FILE_LINES]
    body = "\n".join(f"+ {line}" for line in shown)
    if len(lines) > _MAX_NEW_FILE_LINES:
        body += f"\n… ({len(lines) - _MAX_NEW_FILE_LINES} more lines)"
    return f"(new file, {len(lines)} lines)\n{body}"


async def _check_path_access(sandbox, path: Path) -> Optional[str]:
    """Return an error message when sandbox denies access to *path*, else None."""
    checker = getattr(sandbox, "check_file_access", None) if sandbox else None
    if checker is None:
        return None
    allowed = await checker(str(path))
    if not allowed:
        return f"Path outside allowed workspace: {path}"
    return None


def _decode_bytes(data: bytes) -> tuple[str, str]:
    """Decode file bytes, returning ``(text, encoding)``.

    The encoding is returned so edit/write can put the file back exactly as
    it was: silently rewriting a GBK or UTF-16 source file as UTF-8 corrupts
    it for every other tool (and shows up as a whole-file diff in git).
    """
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace"), "utf-16"
    if b"\x00" in data[:4096]:
        # NUL bytes in "text" strongly suggest UTF-16 without a BOM.
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                text = data.decode(enc)
            except UnicodeDecodeError:
                continue
            printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
            if text and printable / len(text) > 0.90:
                return text, enc
    # UTF-8 first (never mis-detects), then GB18030 — the common encoding of
    # zh-CN Windows projects, which strict-UTF-8 reads used to reject.
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8"


def _read_text(path: Path) -> str:
    """Read a text file, tolerating common Windows encodings."""
    return _decode_bytes(path.read_bytes())[0]


def _normalize_nl(text: str) -> str:
    """Normalise line endings to LF for comparison/diff purposes."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _write_text(path: Path, content: str, encoding: str = "utf-8",
                original: str | None = None) -> None:
    """Write text verbatim in *encoding*, falling back to UTF-8 if a character
    does not fit the file's original code page (new content must never be lost).

    ``newline=""`` disables the platform newline translation that
    ``Path.write_text`` applies: on Windows it silently turned every LF into
    CRLF, so a one-line edit rewrote all line endings.

    When *original* is given and used CRLF, the new content adopts CRLF too —
    editing a Windows file must not flip its whole file's line endings.
    """
    if original is not None and "\r\n" in original:
        content = _normalize_nl(content).replace("\n", "\r\n")
    try:
        with open(path, "w", encoding=encoding, newline="") as handle:
            handle.write(content)
    except (UnicodeEncodeError, LookupError):
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)


class ReadTool(BaseTool):
    """Read file contents."""

    def get_spec(self) -> ToolSpec:
        return ToolSpec(
            name="read",
            description="Read the contents of a file. Returns the file content with line numbers.",
            parameters={
                "file_path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (1-indexed)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read",
                },
            },
            required=["file_path"],
        )

    async def execute(self, file_path: str, offset: int = None, limit: int = None) -> ToolResult:
        try:
            path = Path(file_path).resolve()
            denied = await _check_path_access(self.sandbox, path)
            if denied:
                return ToolResult(success=False, output="", error=denied)
            if not path.exists():
                return ToolResult(success=False, output="", error=f"File not found: {file_path}")
            if not path.is_file():
                return ToolResult(success=False, output="", error=f"Not a file: {file_path}")
            if path.stat().st_size > _MAX_READ_FILE_BYTES:
                return ToolResult(
                    success=False, output="",
                    error=(f"File too large ({path.stat().st_size} bytes > "
                           f"{_MAX_READ_FILE_BYTES}). Use offset/limit to page "
                           f"through it, or grep to locate the relevant lines."))

            # Model-facing text is always LF: a trailing '\r' on every line
            # is noise in the prompt, and the edit tool matches in LF space.
            content = _normalize_nl(_read_text(path))
            lines = content.splitlines(keepends=True)

            start = (offset - 1) if offset and offset > 0 else 0
            if start >= len(lines):
                return ToolResult(
                    success=True,
                    output=(f"(offset {start + 1} is past the end of the file — "
                            f"it has {len(lines)} line(s))"))
            # Unbounded reads used to dump whole generated files into the
            # prompt; default to a page the model can extend explicitly.
            page = limit if limit and limit > 0 else _MAX_READ_LINES
            end = start + page
            selected = lines[start:end]

            # Format with line numbers
            result = []
            for i, line in enumerate(selected, start=start + 1):
                result.append(f"{i:>6}→{line}")

            output = "".join(result)
            if end < len(lines):
                output += (f"\n… (truncated: showing lines {start + 1}-{end} of "
                           f"{len(lines)}. Use offset/limit to read more.)")
            if len(output) > _MAX_READ_CHARS:
                output = (output[:_MAX_READ_CHARS]
                          + f"\n… (truncated at {_MAX_READ_CHARS} chars — "
                            f"narrow the range with offset/limit.)")
            return ToolResult(success=True, output=output)

        except Exception as e:
            return ToolResult(success=False, output="", error=describe_exc(e))


class WriteTool(BaseTool):
    """Write content to a file (honors sandbox.allow_file_write)."""

    def __init__(self, sandbox: Optional[object] = None, allow_write: bool = True):
        super().__init__(sandbox)
        self.allow_write = allow_write

    def get_spec(self) -> ToolSpec:
        return ToolSpec(
            name="write",
            description="Write content to a file. Creates parent directories if needed. OVERWRITES existing content.",
            parameters={
                "file_path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            required=["file_path", "content"],
        )

    async def execute(self, file_path: str, content: str) -> ToolResult:
        if not self.allow_write:
            return ToolResult(
                success=False, output="",
                error="File writes are disabled (sandbox.allow_file_write=false).")
        try:
            path = Path(file_path).resolve()
            denied = await _check_path_access(self.sandbox, path)
            if denied:
                return ToolResult(success=False, output="", error=denied)
            path.parent.mkdir(parents=True, exist_ok=True)
            old = ""
            encoding = "utf-8"
            existed = path.is_file()
            if existed:
                try:
                    old, encoding = _decode_bytes(path.read_bytes())
                except OSError:
                    old, encoding = "", "utf-8"
            # Keep the file's original encoding instead of silently converting
            # GBK/UTF-16 sources to UTF-8 (which rewrites the whole file), and
            # keep its CRLF convention when it had one.
            _write_text(path, content, encoding, original=old if existed else None)
            # "diff before write" — the model (and the user watching the TUI)
            # should see WHAT changed, not just that bytes were written.
            if existed and _normalize_nl(old) != _normalize_nl(content):
                detail = _unified_diff(old, content, file_path)
            elif existed:
                detail = "(no change — file content is identical)"
            else:
                detail = _new_file_preview(content)
            return ToolResult(
                success=True,
                output=f"Written {len(content)} bytes to {file_path}\n{detail}")
        except Exception as e:
            return ToolResult(success=False, output="", error=describe_exc(e))


class EditTool(BaseTool):
    """Edit a file by replacing a specific string (honors allow_file_write)."""

    def __init__(self, sandbox: Optional[object] = None, allow_write: bool = True):
        super().__init__(sandbox)
        self.allow_write = allow_write

    def get_spec(self) -> ToolSpec:
        return ToolSpec(
            name="edit",
            description="Edit a file by replacing exact string matches. Use unique context to ensure single match.",
            parameters={
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to edit",
                },
                "old_string": {
                    "type": "string",
                    "description": "Text to replace (must be unique in the file)",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement text",
                },
            },
            required=["file_path", "old_string", "new_string"],
        )

    async def execute(self, file_path: str, old_string: str, new_string: str) -> ToolResult:
        if not self.allow_write:
            return ToolResult(
                success=False, output="",
                error="File writes are disabled (sandbox.allow_file_write=false).")
        try:
            path = Path(file_path).resolve()
            denied = await _check_path_access(self.sandbox, path)
            if denied:
                return ToolResult(success=False, output="", error=denied)
            if not path.exists():
                return ToolResult(success=False, output="", error=f"File not found: {file_path}")

            content, encoding = _decode_bytes(path.read_bytes())
            crlf = "\r\n" in content
            # Match in LF space: models habitually write "\n" while Windows
            # files use CRLF, and a raw str.replace would report "not found"
            # for text that is visibly present in the file.
            lf = _normalize_nl(content)
            old_lf = _normalize_nl(old_string)
            count = lf.count(old_lf)

            if count == 0:
                return ToolResult(
                    success=False, output="",
                    error=("String not found in file. Check exact whitespace "
                           "and indentation"
                           + (" (the file uses CRLF line endings)." if crlf else ".")))
            if count > 1:
                return ToolResult(success=False, output="", error=f"String found {count} times — use more context")

            new_lf = lf.replace(old_lf, _normalize_nl(new_string), 1)
            new_content = new_lf.replace("\n", "\r\n") if crlf else new_lf
            _write_text(path, new_content, encoding, original=content)

            # Show the actual unified diff (industry-standard "diff before
            # write"): line counts alone cannot reveal a wrong replacement.
            diff = _unified_diff(content, new_content, file_path)
            return ToolResult(success=True, output=f"Edited {file_path}\n{diff}")

        except Exception as e:
            return ToolResult(success=False, output="", error=describe_exc(e))


class LsTool(BaseTool):
    """List directory contents."""

    def get_spec(self) -> ToolSpec:
        return ToolSpec(
            name="ls",
            description="List files and directories in a path.",
            parameters={
                "path": {
                    "type": "string",
                    "description": "Directory path to list",
                },
            },
            required=["path"],
        )

    async def execute(self, path: str = ".") -> ToolResult:
        try:
            p = Path(path).resolve()
            denied = await _check_path_access(self.sandbox, p)
            if denied:
                return ToolResult(success=False, output="", error=denied)
            if not p.exists():
                return ToolResult(success=False, output="", error=f"Path not found: {path}")
            if not p.is_dir():
                return ToolResult(success=False, output="", error=f"Not a directory: {path}")

            entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            lines = []
            for entry in entries:
                prefix = "📁 " if entry.is_dir() else "📄 "
                lines.append(f"{prefix}{entry.name}")
            return ToolResult(success=True, output="\n".join(lines))

        except Exception as e:
            return ToolResult(success=False, output="", error=describe_exc(e))


class GlobTool(BaseTool):
    """Find files by glob pattern."""

    def get_spec(self) -> ToolSpec:
        return ToolSpec(
            name="glob",
            description="Find files matching a glob pattern.",
            parameters={
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern (e.g. '**/*.py', 'src/**/*.ts')",
                },
                "path": {
                    "type": "string",
                    "description": "Root directory to search in",
                },
            },
            required=["pattern"],
        )

    async def execute(self, pattern: str, path: str = ".") -> ToolResult:
        try:
            root = Path(path).resolve()
            denied = await _check_path_access(self.sandbox, root)
            if denied:
                return ToolResult(success=False, output="", error=denied)
            matches = sorted(root.glob(pattern))
            if not matches:
                return ToolResult(success=True, output=f"No files matching '{pattern}'")
            lines = [str(m.relative_to(root)) for m in matches if m.is_file()]
            if not lines:
                return ToolResult(success=True, output=f"No files matching '{pattern}'")
            output = "\n".join(lines[:_MAX_GLOB_RESULTS])
            if len(lines) > _MAX_GLOB_RESULTS:
                # Silent truncation made the model believe it had seen every
                # file; say so, and point at the tool that can narrow it.
                output += (f"\n… ({len(lines) - _MAX_GLOB_RESULTS} more matches "
                           f"not shown — narrow the pattern or use grep.)")
            return ToolResult(success=True, output=output)
        except Exception as e:
            return ToolResult(success=False, output="", error=describe_exc(e))