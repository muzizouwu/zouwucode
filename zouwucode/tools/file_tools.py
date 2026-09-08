"""File operation tools — read, write, edit, list, glob."""

from pathlib import Path
from typing import Optional

from .base import BaseTool, ToolSpec, ToolResult


async def _check_path_access(sandbox, path: Path) -> Optional[str]:
    """Return an error message when sandbox denies access to *path*, else None."""
    checker = getattr(sandbox, "check_file_access", None) if sandbox else None
    if checker is None:
        return None
    allowed = await checker(str(path))
    if not allowed:
        return f"Path outside allowed workspace: {path}"
    return None


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

            content = path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines(keepends=True)

            start = (offset - 1) if offset and offset > 0 else 0
            end = start + limit if limit else len(lines)
            selected = lines[start:end]

            # Format with line numbers
            result = []
            for i, line in enumerate(selected, start=start + 1):
                result.append(f"{i:>6}→{line}")

            output = "".join(result)
            return ToolResult(success=True, output=output)

        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class WriteTool(BaseTool):
    """Write content to a file."""

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
        try:
            path = Path(file_path).resolve()
            denied = await _check_path_access(self.sandbox, path)
            if denied:
                return ToolResult(success=False, output="", error=denied)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return ToolResult(success=True, output=f"Written {len(content)} bytes to {file_path}")
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class EditTool(BaseTool):
    """Edit a file by replacing a specific string."""

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
        try:
            path = Path(file_path).resolve()
            denied = await _check_path_access(self.sandbox, path)
            if denied:
                return ToolResult(success=False, output="", error=denied)
            if not path.exists():
                return ToolResult(success=False, output="", error=f"File not found: {file_path}")

            content = path.read_text(encoding="utf-8")
            count = content.count(old_string)

            if count == 0:
                return ToolResult(success=False, output="", error="String not found in file")
            if count > 1:
                return ToolResult(success=False, output="", error=f"String found {count} times — use more context")

            new_content = content.replace(old_string, new_string, 1)
            path.write_text(new_content, encoding="utf-8")

            # Show diff
            old_lines = content.splitlines()
            new_lines = new_content.splitlines()
            return ToolResult(success=True, output=f"Edited {file_path} ({len(old_lines)} → {len(new_lines)} lines)")

        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


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
            return ToolResult(success=False, output="", error=str(e))


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
            matches = sorted(root.glob(pattern))
            if not matches:
                return ToolResult(success=True, output=f"No files matching '{pattern}'")
            lines = [str(m.relative_to(root)) for m in matches if m.is_file()]
            return ToolResult(success=True, output="\n".join(lines[:200]))  # limit output
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))