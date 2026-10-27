"""v1.9.0 — content search + context-hygiene guards.

Defects pinned here:

1. ``grep`` did not exist, yet ZOUWUCODE's own agent prompts told the model
   to "use grep / glob / read tools" — the model asked for a tool it could
   never get. Search-by-content is the single most used operation in a
   mature coding agent, so it must exist and be reliable.
2. ``read`` / ``bash`` / ``glob`` returned unbounded output. One generated
   or minified file could blow the context window and turn a normal task
   into a stall. Every path is now capped AND says that it truncated.
3. ``edit`` / ``write`` read and wrote UTF-8 only: a GBK or UTF-16 source
   file (common on zh-CN Windows) raised a decode error or was silently
   rewritten in a different encoding. Both now preserve the original.
"""

import asyncio

import pytest

from zouwucode.config import SandboxConfig, ZOUWUCODEConfig
from zouwucode.sandbox.permission import PermissionManager
from zouwucode.tools import search_tools
from zouwucode.tools.file_tools import (
    EditTool,
    GlobTool,
    ReadTool,
    WriteTool,
    _MAX_GLOB_RESULTS,
    _MAX_READ_LINES,
)
from zouwucode.tools.search_tools import GrepTool
from zouwucode.tools.shell_tools import _MAX_STREAM_CHARS, _cap_stream
from zouwucode.runtime import create_builtin_tools


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "def main():\n    print('Hello World')\n", encoding="utf-8")
    (tmp_path / "src" / "util.py").write_text(
        "# helper\nVALUE = 42\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "Hello from the docs\n", encoding="utf-8")
    return tmp_path


class TestGrepTool:
    def test_finds_content_with_file_line_and_text(self, workspace):
        r = run(GrepTool().execute(pattern=r"hello", path=str(workspace),
                                   ignore_case=True))
        assert r.success
        assert "src/app.py:2:" in r.output
        assert "Hello World" in r.output

    def test_include_filter_limits_scope(self, workspace):
        r = run(GrepTool().execute(pattern="Hello", path=str(workspace),
                                   include="*.md"))
        assert r.success
        assert "README.md" in r.output
        assert "app.py" not in r.output

    def test_files_only_returns_paths(self, workspace):
        r = run(GrepTool().execute(pattern="Hello", path=str(workspace),
                                   files_only=True))
        assert r.success
        assert r.output.strip().splitlines() == ["README.md", "src/app.py"]

    def test_no_matches_is_success_not_error(self, workspace):
        r = run(GrepTool().execute(pattern="nonexistent_symbol_xyz",
                                   path=str(workspace)))
        assert r.success
        assert "No matches" in r.output

    def test_invalid_regex_is_a_clear_error(self, workspace):
        r = run(GrepTool().execute(pattern="(unclosed", path=str(workspace)))
        assert not r.success
        assert "Invalid regular expression" in (r.error or "")

    def test_empty_pattern_rejected(self, workspace):
        r = run(GrepTool().execute(pattern="", path=str(workspace)))
        assert not r.success
        assert "must not be empty" in (r.error or "")

    def test_missing_path_reports_reason(self, tmp_path):
        r = run(GrepTool().execute(pattern="x", path=str(tmp_path / "nope")))
        assert not r.success
        assert "Path not found" in (r.error or "")

    def test_binary_files_are_skipped(self, workspace):
        (workspace / "blob.bin").write_bytes(b"\x00\x01needle\x00\x02")
        r = run(GrepTool().execute(pattern="needle", path=str(workspace)))
        assert r.success
        assert "blob.bin" not in r.output

    def test_result_limit_is_announced(self, workspace):
        (workspace / "many.txt").write_text(
            "\n".join(f"hit {i}" for i in range(50)), encoding="utf-8")
        r = run(GrepTool().execute(pattern="hit", path=str(workspace),
                                   max_results=5))
        assert r.success
        assert "result limit reached" in r.output

    def test_python_fallback_matches_rg_output_shape(self, workspace, monkeypatch):
        """The fallback (no ripgrep installed) must produce the same
        ``path:line:text`` contract — environments without rg are common
        on Windows and must not silently degrade the tool."""
        monkeypatch.setattr(search_tools.shutil, "which", lambda _name: None)
        r = run(GrepTool().execute(pattern="VALUE", path=str(workspace)))
        assert r.success
        assert "src/util.py:2:" in r.output

    def test_single_file_target(self, workspace):
        r = run(GrepTool().execute(pattern="VALUE",
                                   path=str(workspace / "src" / "util.py")))
        assert r.success
        assert "util.py:2:" in r.output

    def test_sandbox_denies_outside_workspace(self, workspace, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        sandbox = PermissionManager(SandboxConfig(enabled=True))
        sandbox.set_workspace(other)
        r = run(GrepTool(sandbox).execute(pattern="Hello", path=str(workspace)))
        assert not r.success
        assert "outside allowed workspace" in (r.error or "")


class TestReadGuards:
    def test_default_page_is_bounded_and_announced(self, tmp_path):
        target = tmp_path / "big.txt"
        target.write_text("\n".join(str(i) for i in range(_MAX_READ_LINES + 50)),
                          encoding="utf-8")
        r = run(ReadTool().execute(file_path=str(target)))
        assert r.success
        assert "truncated: showing lines 1-" in r.output
        assert "Use offset/limit" in r.output

    def test_small_file_is_not_truncated(self, tmp_path):
        target = tmp_path / "small.txt"
        target.write_text("a\nb\nc\n", encoding="utf-8")
        r = run(ReadTool().execute(file_path=str(target)))
        assert r.success
        assert "truncated" not in r.output

    def test_offset_past_end_is_explained(self, tmp_path):
        target = tmp_path / "short.txt"
        target.write_text("one\ntwo\n", encoding="utf-8")
        r = run(ReadTool().execute(file_path=str(target), offset=99))
        assert r.success
        assert "past the end" in r.output

    def test_oversized_file_is_refused_with_guidance(self, tmp_path, monkeypatch):
        import zouwucode.tools.file_tools as ft
        monkeypatch.setattr(ft, "_MAX_READ_FILE_BYTES", 10)
        target = tmp_path / "huge.txt"
        target.write_text("x" * 100, encoding="utf-8")
        r = run(ReadTool().execute(file_path=str(target)))
        assert not r.success
        assert "File too large" in (r.error or "")
        assert "offset/limit" in (r.error or "")


class TestGlobGuard:
    def test_truncation_is_announced(self, tmp_path):
        for i in range(_MAX_GLOB_RESULTS + 5):
            (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
        r = run(GlobTool().execute(pattern="*.txt", path=str(tmp_path)))
        assert r.success
        assert "more matches not shown" in r.output


class TestShellGuard:
    def test_cap_stream_keeps_head_and_tail(self):
        text = "H" * 40_000 + "T" * 40_000
        capped = _cap_stream(text)
        assert len(capped) < len(text)
        assert capped.startswith("H")
        assert capped.endswith("T")
        assert "output truncated" in capped

    def test_short_output_untouched(self):
        assert _cap_stream("ok") == "ok"
        assert len(_cap_stream("x" * _MAX_STREAM_CHARS)) == _MAX_STREAM_CHARS


class TestEncodingPreservation:
    def test_gbk_file_can_be_read_and_edited(self, tmp_path):
        target = tmp_path / "gbk.txt"
        target.write_bytes("中文内容\n第二行\n".encode("gbk"))
        tool = EditTool()
        r = run(tool.execute(file_path=str(target),
                             old_string="中文", new_string="汉化"))
        assert r.success, r.error
        assert target.read_bytes().decode("gbk").startswith("汉化内容")

    def test_utf16_file_can_be_read_and_edited(self, tmp_path):
        target = tmp_path / "u16.txt"
        target.write_bytes("hello\nworld\n".encode("utf-16"))
        read = run(ReadTool().execute(file_path=str(target)))
        assert read.success
        assert "hello" in read.output
        r = run(EditTool().execute(file_path=str(target),
                                  old_string="world", new_string="there"))
        assert r.success, r.error
        assert "there" in target.read_bytes().decode("utf-16")

    def test_write_preserves_existing_encoding(self, tmp_path):
        target = tmp_path / "gbk_write.txt"
        target.write_bytes("初始内容\n".encode("gbk"))
        r = run(WriteTool().execute(file_path=str(target), content="替换内容\n"))
        assert r.success
        # Still decodable as GBK (not re-encoded as UTF-8).
        assert target.read_bytes().decode("gbk") == "替换内容\n"


class TestRegistration:
    def test_grep_is_in_the_builtin_toolset(self):
        names = {t.get_name() for t in create_builtin_tools(None, ZOUWUCODEConfig())}
        assert "grep" in names
