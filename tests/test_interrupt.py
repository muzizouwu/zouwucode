"""Tests for the interrupt (打断) feature.

Covers the user-facing scenarios:
- 正常中断: interrupt during LLM streaming / between tool executions
- 异常中断: request_interrupt when idle, abrupt state after interrupt
- 连续中断: repeated interrupt cycles must leave clean state
- 状态稳定: conversation prefix stays API-valid after interruption
- UI 集成: TUI hotkey/modal wiring, WebUI button/endpoint wiring
"""

import asyncio

import pytest

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.engine.cache import PrefixCache
from zouwucode.engine.loop import EngineLoop, TaskInterrupted, TurnLimitExceeded
from zouwucode.engine.providers.base import (
    BaseProvider,
    ModelResponse,
    ToolCall,
    ToolResult,
)


def _engine(provider, **engine_cfg) -> EngineLoop:
    config = ZOUWUCODEConfig()
    for key, value in engine_cfg.items():
        setattr(config.engine, key, value)
    return EngineLoop(config, provider)


class _SlowStreamProvider(BaseProvider):
    """Yields many delayed content chunks — interrupt lands mid-stream."""

    def __init__(self, chunk_count: int = 100, delay: float = 0.05):
        super().__init__({"api_key": "sk-test"})
        self.chunk_count = chunk_count
        self.delay = delay

    async def chat_stream(self, messages, tools=None, temperature=0.0,
                          max_tokens=65536, stream_thinking=True):
        for i in range(self.chunk_count):
            await asyncio.sleep(self.delay)
            yield ModelResponse(content="x")
        yield ModelResponse(content="", tool_calls=[], usage={})

    async def chat(self, messages, tools=None, temperature=0.0,
                   max_tokens=65536):
        return ModelResponse(content="x" * self.chunk_count)


class _MultiToolProvider(BaseProvider):
    """Returns N tool calls per round; the round never converges."""

    def __init__(self, calls_per_round: int = 3):
        super().__init__({"api_key": "sk-test"})
        self.calls_per_round = calls_per_round

    async def chat_stream(self, messages, tools=None, temperature=0.0,
                          max_tokens=65536, stream_thinking=True):
        yield ModelResponse(content="", tool_calls=[
            ToolCall(id=f"c{i}", name="read", arguments="{}")
            for i in range(self.calls_per_round)
        ])
        yield ModelResponse(content="", tool_calls=[], usage={})

    async def chat(self, messages, tools=None, temperature=0.0,
                   max_tokens=65536):
        return ModelResponse(content="")


class TestNormalInterrupt:
    """正常中断 — interrupt lands while the task is running."""

    def test_interrupt_during_llm_stream(self):
        """LLM 流式输出期间打断 → 立即终止，耗时远小于完整流时长。"""
        engine = _engine(_SlowStreamProvider(chunk_count=200, delay=0.05))

        async def scenario():
            task = asyncio.create_task(engine.run(
                messages=[{"role": "user", "content": "hi"}]
            ))
            await asyncio.sleep(0.25)  # let ~5 chunks stream
            assert engine.is_running is True
            assert engine.request_interrupt(reason="test") is True
            with pytest.raises(TaskInterrupted):
                await task
            return asyncio.get_running_loop().time()

        loop = asyncio.new_event_loop()
        try:
            start = loop.time()
            loop.run_until_complete(scenario())
            elapsed = loop.time() - start
        finally:
            loop.close()

        # Full stream would take 200 * 0.05 = 10s; interrupted at ~0.25s.
        assert elapsed < 3.0, f"打断应在亚秒级生效，实际耗时 {elapsed:.2f}s"
        assert engine.is_running is False

    def test_interrupt_between_tools(self):
        """工具执行间隙打断 → 剩余工具不再执行。"""
        engine = _engine(_MultiToolProvider(calls_per_round=5),
                         max_tool_rounds=10)
        executed: list[str] = []

        async def executor(tool_call, context):
            executed.append(tool_call.id)
            if len(executed) == 2:
                engine.request_interrupt(reason="test")
            return ToolResult(tool_call_id=tool_call.id, content="ok")

        engine.set_tool_executor(executor)

        with pytest.raises(TaskInterrupted):
            asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))

        assert executed == ["c0", "c1"], "打断后剩余工具不应执行"
        assert engine.is_running is False

    def test_interrupt_at_round_boundary(self):
        """打断请求在轮间安全点生效（工具全部执行完、下一轮开始前）。"""
        engine = _engine(_MultiToolProvider(calls_per_round=1),
                         max_tool_rounds=10)
        rounds_executed: list[int] = []

        async def executor(tool_call, context):
            rounds_executed.append(len(rounds_executed) + 1)
            if len(rounds_executed) == 2:
                engine.request_interrupt(reason="test")
            return ToolResult(tool_call_id=tool_call.id, content="ok")

        engine.set_tool_executor(executor)

        with pytest.raises(TaskInterrupted):
            asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))

        assert len(rounds_executed) == 2


class TestStateStability:
    """中断后的状态稳定性 — prefix 必须保持 API 合法、事件必须复位。"""

    def test_prefix_valid_after_interrupt_between_tools(self):
        """中断后：assistant 的每个 tool_call 都有配对的 tool result。"""
        engine = _engine(_MultiToolProvider(calls_per_round=5),
                         max_tool_rounds=10)
        executed: list[str] = []

        async def executor(tool_call, context):
            executed.append(tool_call.id)
            if len(executed) == 2:
                engine.request_interrupt(reason="test")
            return ToolResult(tool_call_id=tool_call.id, content="ok")

        engine.set_tool_executor(executor)

        with pytest.raises(TaskInterrupted):
            asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))

        prefix = engine.cache.get_prefix()
        # Locate the last assistant message carrying tool_calls
        assistant_idx = None
        for i, msg in enumerate(prefix):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                assistant_idx = i
        assert assistant_idx is not None, "prefix 中应存在带 tool_calls 的 assistant 消息"

        tool_call_ids = {
            tc["id"] for tc in prefix[assistant_idx]["tool_calls"]
        }
        tool_result_ids = {
            msg["tool_call_id"]
            for msg in prefix[assistant_idx + 1:]
            if msg.get("role") == "tool"
        }
        assert tool_result_ids == tool_call_ids, (
            f"每个 tool_call 都必须有配对的 tool result："
            f"缺少 {tool_call_ids - tool_result_ids}"
        )
        # The synthetic interrupted result must be marked as such
        interrupted = [
            msg for msg in prefix[assistant_idx + 1:]
            if msg.get("role") == "tool" and "interrupted" in msg.get("content", "")
        ]
        assert len(interrupted) == 3, "未执行的 3 个工具应有合成的 interrupted 结果"

    def test_prefix_valid_after_interrupt_mid_stream(self):
        """流式中断（assistant 消息尚未落盘）→ prefix 不残留半截消息。"""
        engine = _engine(_SlowStreamProvider(chunk_count=200, delay=0.05))

        async def scenario():
            task = asyncio.create_task(engine.run(
                messages=[{"role": "user", "content": "hi"}]
            ))
            await asyncio.sleep(0.25)
            engine.request_interrupt(reason="test")
            with pytest.raises(TaskInterrupted):
                await task

        asyncio.run(scenario())

        prefix = engine.cache.get_prefix()
        # user message appended, no assistant/tool fragments left behind
        roles = [m["role"] for m in prefix]
        assert "user" in roles
        assert roles.count("assistant") == 0, "流式中断不应残留 assistant 消息"
        assert roles.count("tool") == 0, "流式中断不应残留 tool 消息"

    def test_interrupt_state_reset_after_interrupt(self):
        """中断后中断事件必须复位 — 否则下一个任务会立即被误杀。"""
        engine = _engine(_SlowStreamProvider(chunk_count=200, delay=0.05))

        async def scenario():
            task = asyncio.create_task(engine.run(
                messages=[{"role": "user", "content": "hi"}]
            ))
            await asyncio.sleep(0.25)
            engine.request_interrupt(reason="test")
            with pytest.raises(TaskInterrupted):
                await task
            assert engine.is_running is False
            assert engine.request_interrupt("idle") is False, "空闲时打断应返回 False"

        asyncio.run(scenario())


class TestConsecutiveInterrupts:
    """连续中断 — 重复打断不能污染后续任务。"""

    def test_two_consecutive_interrupt_cycles(self):
        for cycle in range(2):
            engine = _engine(_SlowStreamProvider(chunk_count=200, delay=0.05))

            async def scenario():
                task = asyncio.create_task(engine.run(
                    messages=[{"role": "user", "content": f"round {cycle}"}]
                ))
                await asyncio.sleep(0.25)
                assert engine.request_interrupt("test") is True
                with pytest.raises(TaskInterrupted):
                    await task

            asyncio.run(scenario())
            assert engine.is_running is False

    def test_task_after_interrupt_completes_normally(self):
        """打断一次后，紧接着的正常任务必须完整跑完（事件复位验证）。"""
        engine = _engine(_SlowStreamProvider(chunk_count=200, delay=0.05))

        async def scenario():
            task = asyncio.create_task(engine.run(
                messages=[{"role": "user", "content": "first"}]
            ))
            await asyncio.sleep(0.25)
            engine.request_interrupt("test")
            with pytest.raises(TaskInterrupted):
                await task

        asyncio.run(scenario())

        # Second run: fast provider, no interrupt — must finish cleanly.
        from zouwucode.engine.loop import EngineLoop as _EL

        class _FastProvider(BaseProvider):
            def __init__(self):
                super().__init__({"api_key": "sk-test"})

            async def chat_stream(self, messages, tools=None, temperature=0.0,
                                  max_tokens=65536, stream_thinking=True):
                yield ModelResponse(content="done")
                yield ModelResponse(content="", tool_calls=[], usage={})

            async def chat(self, messages, tools=None, temperature=0.0,
                           max_tokens=65536):
                return ModelResponse(content="done")

        engine2 = engine  # same engine — proves event reset works
        engine2.provider = _FastProvider()
        resp = asyncio.run(engine2.run(messages=[{"role": "user", "content": "second"}]))
        assert resp.content == "done"

    def test_interrupt_after_limit_abort_still_clean(self):
        """安全限位终止后（非打断），打断 API 保持可用且状态干净。"""
        calls: list = []

        class _LoopProvider(BaseProvider):
            async def chat_stream(self, messages, tools=None, temperature=0.0,
                                  max_tokens=65536, stream_thinking=True):
                yield ModelResponse(content="", tool_calls=[
                    ToolCall(id="c1", name="read", arguments="{}"),
                ])
                yield ModelResponse(content="", tool_calls=[], usage={})

            async def chat(self, messages, tools=None, temperature=0.0,
                           max_tokens=65536):
                return ModelResponse(content="")

        async def executor(tool_call, context):
            calls.append(tool_call.name)
            return ToolResult(tool_call_id=tool_call.id,
                              content="boom", is_error=True)

        engine = _engine(_LoopProvider({"api_key": "sk-test"}),
                         max_tool_rounds=3,
                         max_consecutive_tool_errors=99)
        engine.set_tool_executor(executor)

        with pytest.raises(TurnLimitExceeded):
            asyncio.run(engine.run(messages=[{"role": "user", "content": "hi"}]))

        assert engine.is_running is False
        assert engine.request_interrupt("idle") is False


class TestInterruptEdgeCases:
    """异常中断场景。"""

    def test_request_interrupt_when_idle(self):
        engine = _engine(_SlowStreamProvider())
        assert engine.request_interrupt("no task") is False

    def test_double_request_interrupt_is_idempotent(self):
        """连续两次打断请求不报错（事件 set 幂等）。"""
        engine = _engine(_SlowStreamProvider(chunk_count=200, delay=0.05))

        async def scenario():
            task = asyncio.create_task(engine.run(
                messages=[{"role": "user", "content": "hi"}]
            ))
            await asyncio.sleep(0.25)
            assert engine.request_interrupt("test") is True
            assert engine.request_interrupt("again") is True  # idempotent
            with pytest.raises(TaskInterrupted):
                await task

        asyncio.run(scenario())


class TestTUIInterruptWiring:
    """TUI 打断功能接线 — 快捷键 / 确认弹窗 / 状态提示。"""

    def test_escape_binding_registered(self):
        from zouwucode.tui.textual_app import ZOUWUCODETUI

        app = ZOUWUCODETUI()
        keys = [b.key for b in app.BINDINGS]
        assert "escape" in keys, "TUI 必须注册 Esc 打断快捷键"
        assert "ctrl+x" not in keys, "Ctrl+X 已废弃，不应再注册"

    def test_escape_falls_back_to_focus_when_idle(self):
        """空闲时 Esc 应保持原行为（聚焦输入框），而非报错。"""
        import inspect
        from zouwucode.tui.textual_app import ZOUWUCODETUI

        src = inspect.getsource(ZOUWUCODETUI.action_interrupt)
        assert "action_focus_input" in src, "空闲时 Esc 应回退为聚焦输入框"

    def test_interrupt_api_available_on_tui_engine(self):
        from zouwucode.tui.textual_app import ZOUWUCODETUI

        app = ZOUWUCODETUI()
        assert app.engine.is_running is False
        assert app.engine.request_interrupt("idle") is False

    def test_confirm_screen_has_confirm_and_cancel(self):
        import inspect
        from zouwucode.tui.textual_app import InterruptConfirmScreen

        src = inspect.getsource(InterruptConfirmScreen)
        assert "btn-confirm-interrupt" in src, "确认弹窗必须有「确认打断」按钮"
        assert "btn-cancel-interrupt" in src, "确认弹窗必须有「继续执行」按钮"
        assert "确认打断当前任务" in src, "弹窗必须有明确的确认提示文案"

    def test_tui_handles_task_interrupted_in_message_flow(self):
        """_handle_message 必须有 TaskInterrupted 分支（源码级检查）。"""
        import inspect
        from zouwucode.tui.textual_app import ZOUWUCODETUI

        src = inspect.getsource(ZOUWUCODETUI._handle_message)
        assert "TaskInterrupted" in src, "TUI 消息处理必须捕获 TaskInterrupted"
        assert "恢复选项" in src or "继续" in src, "必须提示恢复选项"


class TestTUIInterruptModalBehavior:
    """TUI 打断弹窗真实行为测试（run_test + pilot，非源码断言）。"""

    async def test_confirm_button_requests_interrupt(self):
        """点击「确认打断」→ 引擎收到打断请求。"""
        from zouwucode.tui.textual_app import ZOUWUCODETUI, InterruptConfirmScreen

        app = ZOUWUCODETUI()
        async with app.run_test() as pilot:
            # 模拟任务执行中
            app._processing = True
            app.engine._running = True

            app.action_interrupt()
            await pilot.pause()
            assert isinstance(app.screen, InterruptConfirmScreen), "应弹出确认弹窗"

            await pilot.click("#btn-confirm-interrupt")
            await pilot.pause()

            assert app.engine._interrupt_event.is_set(), "确认后必须发出打断请求"
            assert app._interrupting is True

    async def test_cancel_button_keeps_task_running(self):
        """点击「继续执行」→ 不打断，任务继续。"""
        from zouwucode.tui.textual_app import ZOUWUCODETUI, InterruptConfirmScreen

        app = ZOUWUCODETUI()
        async with app.run_test() as pilot:
            app._processing = True
            app.engine._running = True

            app.action_interrupt()
            await pilot.pause()
            assert isinstance(app.screen, InterruptConfirmScreen)

            await pilot.click("#btn-cancel-interrupt")
            await pilot.pause()

            assert not app.engine._interrupt_event.is_set(), "取消后不得打断"
            assert app._interrupting is False


class TestWebUIInterruptWiring:
    """WebUI 打断功能接线 — 按钮 / 接口 / SSE 事件。"""

    def test_interrupt_button_in_html(self):
        from zouwucode.webui.server import HTML_TEMPLATE

        assert 'id="interrupt-btn"' in HTML_TEMPLATE, "页面必须有打断按钮"
        assert "interruptTask" in HTML_TEMPLATE, "页面必须绑定打断 JS 函数"

    def test_interrupt_confirm_dialog_in_html(self):
        from zouwucode.webui.server import HTML_TEMPLATE

        assert "confirm(" in HTML_TEMPLATE, "WebUI 打断必须有确认对话框"
        assert "api/interrupt" in HTML_TEMPLATE, "前端必须调用打断接口"
        assert "e.key === 'Escape'" in HTML_TEMPLATE, "前端必须用 Esc 触发打断"
        assert "ctrl+x" not in HTML_TEMPLATE.lower(), "Ctrl+X 已废弃"

    def test_interrupt_route_registered(self):
        import inspect
        from zouwucode.webui.server import WebUIServer

        src = inspect.getsource(WebUIServer)
        assert "/api/interrupt" in src, "服务端必须注册 /api/interrupt 路由"

    def test_sse_interrupted_event(self):
        from zouwucode.webui.server import HTML_TEMPLATE
        import inspect
        from zouwucode.webui.server import WebUIServer

        assert "'interrupted'" in HTML_TEMPLATE, "前端必须处理 interrupted SSE 事件"
        server_src = inspect.getsource(WebUIServer)
        assert "TaskInterrupted" in server_src, "SSE 处理必须捕获 TaskInterrupted"
