"""Tests for python_exec — the CodeAct persistent-REPL action surface."""

import asyncio

import pytest

from zouwucode.tools.code_exec_tool import PythonExecTool


def run_with_tool(fn):
    """Run an async test fn(tool) and close the tool in the SAME event loop.

    Closing from a fresh loop (a second asyncio.run) would leave the
    subprocess transport bound to the already-closed loop and emit
    'Event loop is closed' warnings during GC.
    """
    async def wrapper():
        t = PythonExecTool(None)
        try:
            return await fn(t)
        finally:
            await t.close()
    return asyncio.run(wrapper())


class TestPythonExec:
    def test_persistent_namespace(self):
        async def scenario(tool):
            await tool.execute("x = 41")
            return await tool.execute("print(x + 1)")
        r = run_with_tool(scenario)
        assert r.success and r.output.strip() == "42"

    def test_imports_persist(self):
        async def scenario(tool):
            await tool.execute("import math")
            return await tool.execute("print(math.factorial(5))")
        r = run_with_tool(scenario)
        assert r.output.strip() == "120"

    def test_exception_captured_not_fatal(self):
        async def scenario(tool):
            r1 = await tool.execute("1/0")
            r2 = await tool.execute("print('still alive')")
            return r1, r2
        r1, r2 = run_with_tool(scenario)
        assert "ZeroDivisionError" in r1.output
        assert r2.success and r2.output.strip() == "still alive"

    def test_cjk_output(self):
        r = run_with_tool(lambda tool: tool.execute('print("中文输出 ok")'))
        assert "中文输出 ok" in r.output

    def test_multiline_code(self):
        code = "def f(n):\n    return n * 2\nprint(f(21))"
        r = run_with_tool(lambda tool: tool.execute(code))
        assert r.output.strip() == "42"

    def test_destructive_pattern_blocked(self):
        r = run_with_tool(lambda tool: tool.execute("import os; os.system('rm -rf /')"))
        assert not r.success
        assert "destructive" in (r.error or "").lower()

    def test_timeout_kills_and_recovers(self):
        async def scenario(tool):
            r1 = await tool.execute("import time; time.sleep(30)", timeout=2)
            r2 = await tool.execute("print('reborn')")
            return r1, r2
        r1, r2 = run_with_tool(scenario)
        assert not r1.success and "timed out" in r1.error
        # session restarted with a fresh namespace
        assert r2.success and r2.output.strip() == "reborn"

    def test_empty_code_rejected(self):
        r = run_with_tool(lambda tool: tool.execute("   "))
        assert not r.success

    def test_sandbox_blocks(self):
        class _Deny:
            async def check_command(self, cmd):
                return False

        async def scenario():
            t = PythonExecTool(_Deny())
            try:
                return await t.execute("print(1)")
            finally:
                await t.close()
        r = asyncio.run(scenario())
        assert not r.success and "sandbox" in r.error.lower()
