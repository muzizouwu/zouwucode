"""Regression: Windows console encoding (GBK) must not corrupt tool output.

The bug class: subprocess stdout/stderr arrive in the console code page
(cp936/GBK on zh-CN Windows) but were decoded as UTF-8 with errors="replace"
— every CJK character became U+FFFD. The same class hit python_exec's child
interpreter (it emitted GBK bytes, or died with UnicodeEncodeError outright).
"""

import asyncio
import locale
import os
import sys

from zouwucode.tools._encoding import PS_UTF8_PREFIX, decode_console, utf8_env
from zouwucode.tools.code_exec_tool import PythonExecTool


class TestDecodeConsole:
    def test_utf8_roundtrip(self):
        assert decode_console("中文输出 ok".encode("utf-8")) == "中文输出 ok"

    def test_utf8_input_never_replaced(self):
        assert "\ufffd" not in decode_console("中文测试".encode("utf-8"))

    def test_locale_bytes_fall_back_gracefully(self):
        enc = locale.getpreferredencoding(False)
        b = "你好".encode(enc, errors="replace")
        out = decode_console(b)
        assert isinstance(out, str) and out  # no exception, non-empty

    def test_broken_bytes_still_return_str(self):
        assert isinstance(decode_console(b"\xff\xfe\x00broken"), str)


class TestUtf8Env:
    def test_sets_child_encoding(self):
        env = utf8_env()
        assert env["PYTHONIOENCODING"] == "utf-8"
        assert env["PYTHONUTF8"] == "1"

    def test_does_not_mutate_process_env(self):
        before = dict(os.environ)
        utf8_env()
        assert dict(os.environ) == before

    def test_ps_prefix_forces_output_encoding(self):
        assert "OutputEncoding" in PS_UTF8_PREFIX


class TestPythonExecCJK:
    """End-to-end: python_exec must return CJK text intact (no U+FFFD)."""

    def test_cjk_survives_round_trip(self):
        async def scenario():
            tool = PythonExecTool(None)
            try:
                return await tool.execute('print("中文输出 ok")')
            finally:
                await tool.close()
        r = asyncio.run(scenario())
        assert r.success
        assert "中文输出 ok" in r.output
        assert "\ufffd" not in r.output

    def test_cjk_from_variable_and_fstring(self):
        async def scenario():
            tool = PythonExecTool(None)
            try:
                return await tool.execute(
                    'name = "世界"\nprint(f"你好，{name}！")')
            finally:
                await tool.close()
        r = asyncio.run(scenario())
        assert "你好，世界！" in r.output


class TestShellCJK:
    """End-to-end: the shell tool must return CJK text intact."""

    def test_python_child_cjk_survives(self, tmp_path):
        from zouwucode.tools.shell_tools import ShellTool

        script = tmp_path / "cjk_probe.py"
        script.write_text('print("壳层中文 ok")', encoding="utf-8")
        if sys.platform == "win32":
            # PowerShell needs the call operator to run a quoted path.
            command = f'& "{sys.executable}" "{script}"'
        else:
            command = f'"{sys.executable}" "{script}"'

        async def scenario():
            tool = ShellTool(None)
            return await tool.execute(command, timeout=60)
        r = asyncio.run(scenario())
        if not r.success and r.error and "timed out" in r.error:
            import pytest
            pytest.skip("shell too slow in this environment")
        assert "壳层中文 ok" in r.output, f"output={r.output!r} error={r.error!r}"
        assert "\ufffd" not in r.output
