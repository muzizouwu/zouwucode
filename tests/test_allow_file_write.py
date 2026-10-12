"""Regression: sandbox.allow_file_write actually gates write/edit.

Defect pinned: the config field was declared but never read — nothing
consulted it, so writes were allowed regardless of the setting.
"""

import asyncio

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.runtime import create_builtin_tools
from zouwucode.tools.file_tools import EditTool, WriteTool


class TestWriteGating:
    def test_default_allows_write(self, tmp_path):
        tool = WriteTool()
        target = tmp_path / "a.txt"
        r = asyncio.run(tool.execute(file_path=str(target), content="hi"))
        assert r.success and target.exists()

    def test_disabled_blocks_write_and_touches_nothing(self, tmp_path):
        tool = WriteTool(None, allow_write=False)
        target = tmp_path / "a.txt"
        r = asyncio.run(tool.execute(file_path=str(target), content="hi"))
        assert not r.success
        assert "allow_file_write" in (r.error or "")
        assert not target.exists()

    def test_disabled_blocks_edit(self, tmp_path):
        target = tmp_path / "b.txt"
        target.write_text("hello", encoding="utf-8")
        tool = EditTool(None, allow_write=False)
        r = asyncio.run(tool.execute(
            file_path=str(target), old_string="hello", new_string="bye"))
        assert not r.success
        assert "allow_file_write" in (r.error or "")
        assert target.read_text(encoding="utf-8") == "hello"

    def test_default_allows_edit(self, tmp_path):
        target = tmp_path / "c.txt"
        target.write_text("hello", encoding="utf-8")
        tool = EditTool()
        r = asyncio.run(tool.execute(
            file_path=str(target), old_string="hello", new_string="bye"))
        assert r.success
        assert target.read_text(encoding="utf-8") == "bye"


class TestRuntimeWiring:
    def _by_name(self, tools, name):
        return [t for t in tools if t.get_name() == name][0]

    def test_create_builtin_tools_reads_config(self):
        cfg = ZOUWUCODEConfig()
        cfg.sandbox.allow_file_write = False
        tools = create_builtin_tools(None, cfg)
        assert self._by_name(tools, "write").allow_write is False
        assert self._by_name(tools, "edit").allow_write is False

    def test_default_config_still_allows(self):
        tools = create_builtin_tools(None, ZOUWUCODEConfig())
        assert self._by_name(tools, "write").allow_write is True
        assert self._by_name(tools, "edit").allow_write is True
