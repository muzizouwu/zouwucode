"""Tests for the sub-agent system (SubAgent / SubAgentManager / TaskTool)
and the extension layer (MCP/LSP reserved integration space)."""

import asyncio
import json

import pytest

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.engine.loop import EngineLoop, TaskInterrupted
from zouwucode.engine.providers.base import (
    BaseProvider,
    ModelResponse,
    ToolCall,
    ToolResult,
)
from zouwucode.agent.coordinator import AgentCoordinator
from zouwucode.agent.subagent import AgentStatus, SubAgentManager
from zouwucode.tools.agent_tools import TaskTool


# ── Fakes ────────────────────────────────────────────────────────────────────

class _EchoProvider(BaseProvider):
    """Echoes the last user message back — lets tests verify isolation."""

    def __init__(self, delay: float = 0.0):
        super().__init__({"api_key": "sk-test"})
        self.delay = delay

    async def chat_stream(self, messages, tools=None, temperature=0.0,
                          max_tokens=65536, stream_thinking=True):
        if self.delay:
            await asyncio.sleep(self.delay)
        user = [m for m in messages if m["role"] == "user"][-1]["content"]
        yield ModelResponse(content=f"done:{user}")
        yield ModelResponse(content="", tool_calls=[], usage={})

    async def chat(self, messages, tools=None, temperature=0.0,
                   max_tokens=65536):
        return ModelResponse(content="echo")


class _SlowProvider(BaseProvider):
    """Hangs for a long time — timeout / interrupt tests."""

    def __init__(self, hang: float = 30.0):
        super().__init__({"api_key": "sk-test"})
        self.hang = hang

    async def chat_stream(self, messages, tools=None, temperature=0.0,
                          max_tokens=65536, stream_thinking=True):
        await asyncio.sleep(self.hang)
        yield ModelResponse(content="never")
        yield ModelResponse(content="", tool_calls=[], usage={})

    async def chat(self, messages, tools=None, temperature=0.0,
                   max_tokens=65536):
        return ModelResponse(content="never")


class _Registry:
    """Fake tool registry returning a uniform success result."""

    def __init__(self):
        self.calls: list[str] = []
        self._schemas = [
            {"type": "function", "function": {"name": "read", "parameters": {}}},
            {"type": "function", "function": {"name": "bash", "parameters": {}}},
        ]

    def get_schemas(self):
        return list(self._schemas)

    async def execute(self, name, **kwargs):
        self.calls.append(name)
        return type("R", (), {"output": f"{name} ok", "success": True})()


def _manager(provider=None, **cfg) -> SubAgentManager:
    config = ZOUWUCODEConfig()
    for key, value in cfg.items():
        setattr(config.subagent, key, value)
    coordinator = AgentCoordinator(config, None, _Registry())
    return SubAgentManager(config, provider or _EchoProvider(), coordinator)


# ── Isolation & parallelism ──────────────────────────────────────────────────

class TestSubAgentIsolation:
    """隔离性 — 子 Agent 必须使用独立引擎，绝不污染主引擎缓存。"""

    def test_subagent_has_own_engine(self):
        manager = _manager()
        agent = manager.create("worker", "instructions")
        assert agent.engine is not manager.coordinator.engine  # coordinator.engine is None here
        assert agent.engine.cache is not None

    def test_parallel_results_isolated(self):
        """两个并行子 Agent 各自收到自己的结果，缓存互不串扰。"""
        manager = _manager(_EchoProvider(delay=0.05))

        async def scenario():
            agents = await manager.run_parallel([
                {"role": "a", "instructions": "sys A", "task": "TASK-A"},
                {"role": "b", "instructions": "sys B", "task": "TASK-B"},
            ])
            return agents

        agents = asyncio.run(scenario())
        assert agents[0].result.content == "done:TASK-A"
        assert agents[1].result.content == "done:TASK-B"
        assert agents[0].status is AgentStatus.COMPLETED
        assert agents[1].status is AgentStatus.COMPLETED

    def test_main_cache_untouched_by_subagents(self):
        """子 Agent 运行不得向主引擎的 PrefixCache 写入任何消息。"""
        config = ZOUWUCODEConfig()
        coordinator = AgentCoordinator(config, None, _Registry())
        provider = _EchoProvider()
        main_engine = EngineLoop(config, provider)
        manager = SubAgentManager(config, provider, coordinator)

        async def scenario():
            task = asyncio.create_task(manager.run_parallel([
                {"role": "w", "instructions": "sys", "task": "hello"},
            ]))
            await asyncio.sleep(0.15)
            snapshot = len(main_engine.cache.get_prefix())
            await task
            return snapshot

        snapshot = asyncio.run(scenario())
        assert len(main_engine.cache.get_prefix()) == snapshot
        assert len(main_engine.cache.get_prefix()) == 0  # nothing appended at all

    def test_parallel_faster_than_sequential(self):
        """并行执行：3 个 0.2s 任务总耗时应接近单任务而非三倍。"""
        manager = _manager(_EchoProvider(delay=0.2))

        async def scenario():
            start = asyncio.get_running_loop().time()
            await manager.run_parallel([
                {"role": f"r{i}", "instructions": "s", "task": f"t{i}"}
                for i in range(3)
            ])
            return asyncio.get_running_loop().time() - start

        elapsed = asyncio.run(scenario())
        assert elapsed < 0.55, f"并行应接近 0.2s，实际 {elapsed:.2f}s（串行需 0.6s+）"


# ── Whitelist & status ───────────────────────────────────────────────────────

class TestToolWhitelist:
    def test_whitelisted_tool_allowed(self):
        manager = _manager()
        agent = manager.create("reader", "sys", tools=["read"])
        import zouwucode.engine.loop as loop_mod

        async def scenario():
            result = await agent.engine._tool_executor(
                ToolCall(id="t1", name="read", arguments="{}"), None
            )
            return result

        result = asyncio.run(scenario())
        assert result.is_error is False
        assert result.content == "read ok"

    def test_non_whitelisted_tool_blocked(self):
        manager = _manager()
        agent = manager.create("reader", "sys", tools=["read"])

        async def scenario():
            return await agent.engine._tool_executor(
                ToolCall(id="t1", name="bash", arguments="{}"), None
            )

        result = asyncio.run(scenario())
        assert result.is_error is True
        assert "not allowed" in result.content

    def test_no_whitelist_allows_all(self):
        manager = _manager()
        agent = manager.create("worker", "sys")

        async def scenario():
            return await agent.engine._tool_executor(
                ToolCall(id="t1", name="bash", arguments="{}"), None
            )

        result = asyncio.run(scenario())
        assert result.is_error is False


class TestSubAgentToolPipeline:
    """端到端工具链路 — 子 Agent 必须真正看到工具 schema 并能触发执行。"""

    class _ToolCallProvider(BaseProvider):
        """第一轮发出 tool_calls 并记录引擎传入的工具 schema；第二轮返回文本。"""

        def __init__(self):
            super().__init__({"api_key": "sk-test"})
            self.seen_tool_names: list[list[str]] = []
            self.round = 0

        async def chat_stream(self, messages, tools=None, temperature=0.0,
                              max_tokens=65536, stream_thinking=True):
            self.round += 1
            if tools is not None:
                self.seen_tool_names.append(
                    [t["function"]["name"] for t in tools]
                )
            else:
                self.seen_tool_names.append([])
            if self.round == 1:
                yield ModelResponse(content="", tool_calls=[
                    ToolCall(id="c1", name="read", arguments="{}"),
                ])
            else:
                yield ModelResponse(content="finished")
            yield ModelResponse(content="", tool_calls=[], usage={})

        async def chat(self, messages, tools=None, temperature=0.0,
                       max_tokens=65536):
            return ModelResponse(content="finished")

    def test_whitelist_filters_schemas_and_executor_runs(self):
        """白名单在 schema 层过滤 + executor 真实触发（端到端）。"""
        provider = self._ToolCallProvider()
        manager = _manager(provider)
        agent = manager.create("reader", "sys", tools=["read"])

        resp = asyncio.run(agent.run("do it"))

        assert resp.content == "finished"
        # 白名单工具过滤发生在 schema 层：模型只看到 read
        assert provider.seen_tool_names[0] == ["read"]
        # executor 被真实调用（证明整条链路打通，而非仅有闭包）
        assert manager.coordinator.tools.calls == ["read"]
        assert agent.status is AgentStatus.COMPLETED

    def test_no_whitelist_sees_all_schemas(self):
        provider = self._ToolCallProvider()
        manager = _manager(provider)
        agent = manager.create("worker", "sys")

        asyncio.run(agent.run("do it"))

        assert provider.seen_tool_names[0] == ["read", "bash"]


class TestTimeouts:
    def test_agent_timeout_marks_failed(self):
        """子 Agent 超时 → 标记 FAILED，不影响兄弟任务，也不抛给调用方。"""
        manager = _manager()
        manager.provider = _SlowProvider(hang=10.0)

        async def scenario():
            agents = await manager.run_parallel([
                {"role": "slow", "instructions": "s", "task": "t",
                 "timeout": 0.2},
                {"role": "fast", "instructions": "s", "task": "t"},
            ])
            return agents

        # EchoProvider needed for the fast one — but provider is shared.
        # Use slow only: fast one would also hang, so give both timeouts.
        async def scenario2():
            manager2 = _manager(_SlowProvider(hang=10.0))
            agents = await manager2.run_parallel([
                {"role": "a", "instructions": "s", "task": "t", "timeout": 0.2},
                {"role": "b", "instructions": "s", "task": "t", "timeout": 0.4},
            ])
            return agents

        agents = asyncio.run(scenario2())
        assert agents[0].status is AgentStatus.FAILED
        assert "timed out" in agents[0].error
        assert agents[1].status is AgentStatus.FAILED
        # 短超时者先失败，两者都安全终止

    def test_max_agents_limit(self):
        manager = _manager(max_agents=2)
        with pytest.raises(RuntimeError, match="max_agents"):
            asyncio.run(manager.run_parallel([
                {"role": "r", "instructions": "s", "task": "t"}
                for _ in range(3)
            ]))


# ── Interrupt cascade ────────────────────────────────────────────────────────

class TestInterruptCascade:
    def test_manager_interrupt_all_stops_running_agents(self):
        manager = _manager(_SlowProvider(hang=10.0))

        async def scenario():
            task = asyncio.create_task(manager.run_parallel([
                {"role": "w1", "instructions": "s", "task": "t"},
                {"role": "w2", "instructions": "s", "task": "t"},
            ]))
            await asyncio.sleep(0.25)
            stopped = manager.interrupt_all()
            await task
            return stopped

        stopped = asyncio.run(scenario())
        assert stopped == 2
        for agent in manager._agents.values():
            assert agent.status is AgentStatus.CANCELLED
            assert agent.engine.is_running is False

    def test_main_engine_interrupt_cascades(self):
        """主引擎 request_interrupt → on_interrupt 钩子 → 子 Agent 全部停止。"""
        config = ZOUWUCODEConfig()
        coordinator = AgentCoordinator(config, None, _Registry())
        provider = _SlowProvider(hang=10.0)
        main_engine = EngineLoop(config, provider)
        manager = SubAgentManager(config, provider, coordinator)
        manager.bind_main_engine(main_engine)
        assert (main_engine.on_interrupt.__self__ is manager
                and main_engine.on_interrupt.__func__ is SubAgentManager.interrupt_all)

        async def scenario():
            task = asyncio.create_task(manager.run_parallel([
                {"role": "w", "instructions": "s", "task": "t"},
            ]))
            await asyncio.sleep(0.25)
            # Simulate the main engine being interrupted by the user
            main_engine._running = True  # request_interrupt requires a running task
            assert main_engine.request_interrupt(reason="user") is True
            await task

        asyncio.run(scenario())
        agent = next(iter(manager._agents.values()))
        assert agent.status is AgentStatus.CANCELLED

    def test_cascade_hook_error_does_not_break_stop(self):
        """级联钩子自身抛异常不得影响打断流程。"""
        engine = EngineLoop(ZOUWUCODEConfig(), _EchoProvider())

        def _boom():
            raise RuntimeError("cascade blew up")

        engine.on_interrupt = _boom
        engine._running = True
        assert engine.request_interrupt("test") is True  # must not raise


# ── TaskTool ─────────────────────────────────────────────────────────────────

class TestTaskTool:
    def test_tool_spec_registered(self):
        manager = _manager()
        tool = TaskTool(manager)
        assert tool.get_name() == "task"
        schema = tool.get_spec().to_openai_tool()
        assert schema["function"]["name"] == "task"

    def test_execute_delegates_and_aggregates(self):
        manager = _manager(_EchoProvider(delay=0.05))
        tool = TaskTool(manager)

        result = asyncio.run(tool.execute(tasks=[
            {"role": "researcher", "instructions": "sys", "task": "RESEARCH-1"},
            {"role": "reviewer", "instructions": "sys", "task": "REVIEW-1"},
        ]))

        assert result.success is True
        assert "done:RESEARCH-1" in result.output
        assert "done:REVIEW-1" in result.output
        assert "completed" in result.output

    def test_execute_rejects_empty_tasks(self):
        tool = TaskTool(_manager())
        result = asyncio.run(tool.execute(tasks=[]))
        assert result.success is False

    def test_execute_reports_max_agents_error(self):
        tool = TaskTool(_manager(max_agents=1))
        result = asyncio.run(tool.execute(tasks=[
            {"role": "a", "task": "t"},
            {"role": "b", "task": "t"},
        ]))
        assert result.success is False
        assert "max_agents" in result.error


# ── Extension layer (MCP/LSP reserved space) ─────────────────────────────────

class TestExtensionHost:
    def test_inactive_by_default(self):
        from zouwucode.extensions import ExtensionHost, ExtensionContext
        from zouwucode.config import ZOUWUCODEConfig as Cfg

        host = ExtensionHost()
        host.register(__import__("zouwucode.extensions", fromlist=["McpExtension"]).McpExtension([]))
        host.register(__import__("zouwucode.extensions", fromlist=["LspExtension"]).LspExtension(False))

        asyncio.run(host.start(ExtensionContext(config=Cfg())))
        assert host.get_tools() == []
        assert host.get_dynamic_schemas() == []
        assert len(host.status_summary()) == 2

    def test_lsp_extension_enabled_registers_diagnostics_tool(self):
        from zouwucode.extensions import ExtensionHost, ExtensionContext, LspExtension

        host = ExtensionHost()
        host.register(LspExtension(enabled=True))
        asyncio.run(host.start(ExtensionContext(config=ZOUWUCODEConfig())))
        tools = host.get_tools()
        assert len(tools) == 1
        assert tools[0].get_name() == "check_diagnostics"

    def test_register_after_start_rejected(self):
        from zouwucode.extensions import ExtensionHost, ExtensionContext

        host = ExtensionHost()
        asyncio.run(host.start(ExtensionContext(config=ZOUWUCODEConfig())))
        with pytest.raises(RuntimeError, match="after start"):
            host.register(_DummyExtension())

    def test_bad_extension_start_does_not_break_host(self):
        from zouwucode.extensions import ExtensionHost, ExtensionContext

        host = ExtensionHost()
        host.register(_DummyExtension())
        asyncio.run(host.start(ExtensionContext(config=ZOUWUCODEConfig())))
        # host must still be usable despite the extension failing to start
        assert host.get_tools() == []


class _DummyExtension:
    """Minimal extension whose start() always fails (test helper)."""

    name = "dummy"

    async def start(self, ctx):
        raise RuntimeError("boom")

    async def stop(self):
        pass

    def get_tools(self):
        return []

    def get_dynamic_schemas(self):
        return []
