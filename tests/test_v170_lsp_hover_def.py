"""Regression for v1.7.0: LSP hover + go-to-definition (richer code intel).

Added hover/definition to the LSP client (diagnostics existed) and exposed two
new tools. These tests use fakes — they never need a real language server.
"""

import asyncio
from pathlib import Path

from zouwucode.lsp.client import LSPClient
from zouwucode.extensions.lsp_ext import LspHoverTool, LspDefinitionTool, LspExtension


class TestFormatting:
    def test_hover_plain_string(self):
        assert LSPClient._format_hover(["plain"]) == "plain"

    def test_hover_markup_list(self):
        h = {"contents": [{"language": "python", "value": "def f() -> int"},
                          "docs here"]}
        out = LSPClient._format_hover(h)
        assert "def f() -> int" in out and "docs here" in out

    def test_hover_markupobject(self):
        assert LSPClient._format_hover({"contents": {"value": "int"}}) == "int"

    def test_hover_empty(self):
        assert LSPClient._format_hover(None) == ""

    def test_uri_to_path(self):
        p = LSPClient._uri_to_path("file:///C:/proj/a.py")
        assert p.endswith("a.py") and "\\\\" not in p


class _FakeLSP:
    def __init__(self, hover="", defs=None):
        self._hover = hover
        self._defs = defs or []
        self.calls = []

    async def hover(self, file_path, line, character, content=""):
        self.calls.append(("hover", file_path, line, character))
        return self._hover

    async def definition(self, file_path, line, character, content=""):
        self.calls.append(("def", file_path, line, character))
        return self._defs


class TestLspTools:
    def test_hover_tool_returns_text_and_converts_to_zero_based(self):
        fake = _FakeLSP(hover="def greet(name: str) -> str")
        r = asyncio.run(LspHoverTool(fake).execute(file_path="a.py", line=3, column=5))
        assert r.success and "greet" in r.output
        assert fake.calls[0] == ("hover", "a.py", 2, 4)          # 1-based -> 0-based

    def test_hover_tool_empty(self):
        r = asyncio.run(LspHoverTool(_FakeLSP()).execute(file_path="a.py", line=1))
        assert r.success and "No hover" in r.output

    def test_definition_tool_lists_locations(self):
        fake = _FakeLSP(defs=[{"file": "C:/p/x.py", "line": 9, "character": 4}])
        r = asyncio.run(LspDefinitionTool(fake).execute(file_path="a.py", line=2))
        assert r.success and "x.py:10:5" in r.output            # 0-based -> display 1-based

    def test_definition_tool_empty(self):
        r = asyncio.run(LspDefinitionTool(_FakeLSP()).execute(file_path="a.py", line=1))
        assert r.success and "No definition" in r.output

    def test_tools_error_becomes_tool_error(self):
        class Boom(_FakeLSP):
            async def hover(self, *a, **k):
                raise RuntimeError("no server")

        r = asyncio.run(LspHoverTool(Boom()).execute(file_path="a.py", line=1))
        assert not r.success and "no server" in (r.error or "")


class TestExtensionRegistration:
    def test_enabled_contributes_three_tools(self):
        from zouwucode.extensions.host import ExtensionContext
        from zouwucode.config import ZOUWUCODEConfig
        ext = LspExtension(enabled=True)
        try:
            asyncio.run(ext.start(ExtensionContext(config=ZOUWUCODEConfig())))
            names = {t.get_name() for t in ext.get_tools()}
            assert names == {"check_diagnostics", "lsp_hover", "lsp_definition"}
        finally:
            asyncio.run(ext.stop())

    def test_disabled_contributes_nothing(self):
        from zouwucode.extensions.host import ExtensionContext
        from zouwucode.config import ZOUWUCODEConfig
        ext = LspExtension(enabled=False)
        asyncio.run(ext.start(ExtensionContext(config=ZOUWUCODEConfig())))
        assert ext.get_tools() == []
