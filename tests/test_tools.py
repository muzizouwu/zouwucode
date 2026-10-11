"""Tests for the tools module."""

import pytest
import tempfile
from pathlib import Path

from zouwucode.tools.file_tools import ReadTool, WriteTool, EditTool, LsTool, GlobTool
from zouwucode.tools.registry import ToolRegistry


@pytest.fixture
def temp_workspace():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


class TestFileTools:
    """Tests for file operation tools."""

    @pytest.mark.asyncio
    async def test_write_and_read(self, temp_workspace):
        write = WriteTool()
        read = ReadTool()

        file_path = str(temp_workspace / "test.txt")
        result = await write.execute(file_path=file_path, content="Hello, World!")
        assert result.success

        result = await read.execute(file_path=file_path)
        assert result.success
        assert "Hello, World!" in result.output

    @pytest.mark.asyncio
    async def test_read_nonexistent(self):
        read = ReadTool()
        result = await read.execute(file_path="/nonexistent/path/file.txt")
        assert not result.success

    @pytest.mark.asyncio
    async def test_edit_file(self, temp_workspace):
        write = WriteTool()
        edit = EditTool()

        file_path = str(temp_workspace / "edit.txt")
        await write.execute(file_path=file_path, content="Hello, World!\nGoodbye!")

        result = await edit.execute(
            file_path=file_path,
            old_string="Hello",
            new_string="Hi",
        )
        assert result.success

        result = await write.execute(file_path=file_path, content="A\nB\nC")
        result = await edit.execute(
            file_path=file_path,
            old_string="B",
            new_string="X",
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_edit_nonexistent_string(self, temp_workspace):
        write = WriteTool()
        edit = EditTool()

        file_path = str(temp_workspace / "nonexistent_edit.txt")
        await write.execute(file_path=file_path, content="Hello")

        result = await edit.execute(
            file_path=file_path,
            old_string="Nonexistent",
            new_string="Something",
        )
        assert not result.success

    @pytest.mark.asyncio
    async def test_ls(self, temp_workspace):
        ls = LsTool()
        # Create some files
        (temp_workspace / "file1.txt").write_text("content1")
        (temp_workspace / "file2.txt").write_text("content2")

        result = await ls.execute(path=str(temp_workspace))
        assert result.success

    @pytest.mark.asyncio
    async def test_glob(self, temp_workspace):
        glob = GlobTool()

        # Create some files
        (temp_workspace / "test1.py").write_text("content1")
        (temp_workspace / "test2.py").write_text("content2")
        (temp_workspace / "data.json").write_text("{}")

        result = await glob.execute(pattern="*.py", path=str(temp_workspace))
        assert result.success
        assert "test1.py" in result.output
        assert "test2.py" in result.output
        assert "data.json" not in result.output


class TestToolRegistry:
    """Tests for the ToolRegistry class."""

    def test_register_and_get(self, temp_workspace):
        registry = ToolRegistry()
        read = ReadTool()
        write = WriteTool()

        registry.register(read)
        registry.register(write)

        assert registry.get("read") is read
        assert registry.get("write") is write
        assert registry.get("nonexistent") is None

    def test_get_schemas(self, temp_workspace):
        registry = ToolRegistry()
        registry.register(ReadTool())

        schemas = registry.get_schemas()
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "read"

    def test_get_names(self, temp_workspace):
        registry = ToolRegistry()
        registry.register(ReadTool())
        registry.register(WriteTool())

        names = registry.get_names()
        assert "read" in names
        assert "write" in names


class TestWebToolErrorText:
    """httpx ConnectTimeout/ReadTimeout have empty str() — the old code
    surfaced a blank error the model couldn't act on (looked like a hang)."""

    def test_describe_exc_fills_empty_httpx_timeout(self):
        import httpx
        from zouwucode.tools.web_tools import _describe_exc
        text = _describe_exc(httpx.ConnectTimeout(""))
        assert "ConnectTimeout" in text
        assert text.strip() != "ConnectTimeout:"   # has a real reason
        assert "连接超时" in text or "timeout" in text.lower()

    def test_describe_exc_keeps_nonempty_message(self):
        from zouwucode.tools.web_tools import _describe_exc
        err = ValueError("boom detail")
        assert "boom detail" in _describe_exc(err)

    def test_search_failure_error_is_nonempty(self):
        """A fully-failed search must return a non-empty actionable error."""
        import asyncio
        from zouwucode.tools import web_tools
        from zouwucode.tools.web_tools import WebSearchTool

        # force every engine to fail
        orig = web_tools._SEARCH_ENGINES
        web_tools._SEARCH_ENGINES = (("bing", "http://x"),
                                     ("duckduckgo", "http://y"))
        try:
            tool = WebSearchTool(None)
            # patch httpx client get to raise
            import httpx
            real = httpx.AsyncClient.get

            async def fake_get(self, *a, **k):
                raise OSError("no net")
            httpx.AsyncClient.get = fake_get
            try:
                res = asyncio.run(tool.execute("q"))
            finally:
                httpx.AsyncClient.get = real
            assert not res.success
            assert res.error and res.error.strip()
            assert "web_fetch" in res.error   # suggests the fallback
        finally:
            web_tools._SEARCH_ENGINES = orig


class TestShellPlatform:
    def test_posix_hint_regex_matches_powershell_breakers(self):
        from zouwucode.tools.shell_tools import _POSIX_HINT
        assert _POSIX_HINT.search("pwd && ls")
        assert _POSIX_HINT.search("cmd 2>/dev/null")
        assert _POSIX_HINT.search("echo $(whoami)")
        assert not _POSIX_HINT.search("Get-Location")

    def test_spec_declares_host_platform(self):
        import sys
        from zouwucode.tools.shell_tools import ShellTool
        desc = ShellTool().get_spec().description
        if sys.platform == "win32":
            assert "PowerShell" in desc
        else:
            assert "bash" in desc