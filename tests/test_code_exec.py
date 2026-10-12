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

    def test_shell_like_python_not_blocked(self):
        """Regression: python_exec must NOT run shell deny-patterns against
        Python source (they false-positive on `format`, `reg`, `dd`... which
        appear in ordinary code). Only the destructive _RISKY screen applies."""
        code = "import re\nm = re.search(r'(reg\\s|regedit)', 'reg add HKLM')\nprint(bool(m))"
        r = run_with_tool(lambda tool: tool.execute(code))
        assert r.success, r.error
        assert "True" in r.output

    def test_parallel_sessions_isolated(self):
        """Different ContextVar sessions get independent REPLs: globals do
        not leak between concurrent workers (mirrors parallel sub-agents)."""
        from zouwucode.tools.code_exec_tool import (
            set_exec_session, reset_exec_session)

        async def scenario(tool):
            async def worker(name):
                # asyncio.gather copies the current Context per child task,
                # so each worker's set_exec_session is private to itself —
                # exactly how SubAgent.run binds a session.
                tok = set_exec_session(name)
                try:
                    await tool.execute(f"v = '{name}'")
                    await asyncio.sleep(0)   # force interleaving
                    r = await tool.execute("print(v)")
                    return r.output.strip()
                finally:
                    reset_exec_session(tok)

            return await asyncio.gather(worker("A"), worker("B"))

        a, b = run_with_tool(scenario)
        assert a == "A" and b == "B"   # no cross-pollution of `v`
