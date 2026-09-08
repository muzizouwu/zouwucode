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