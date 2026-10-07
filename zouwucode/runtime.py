"""Shared runtime factories — single source of truth for CLI / TUI / Web.

Both UI application classes used to carry identical copies of the provider
factory and the builtin tool list, which had already started to drift.
Anything that must be identical across the three UIs belongs here.
"""

from .config import ZOUWUCODEConfig, ProviderConfig
from .engine.providers.base import BaseProvider
from .engine.providers.deepseek import DeepSeekProvider
from .engine.providers.openai import OpenAIProvider
from .tools.base import BaseTool
from .tools.file_tools import ReadTool, WriteTool, EditTool, LsTool, GlobTool
from .tools.shell_tools import ShellTool
from .tools.git_tools import GitTool
from .tools.web_tools import WebSearchTool, WebFetchTool
from .tools.code_exec_tool import PythonExecTool


def create_provider(config: ZOUWUCODEConfig) -> BaseProvider:
    """Build the configured LLM provider (defaults injected if missing)."""
    if config.default_provider not in config.providers:
        config.providers[config.default_provider] = ProviderConfig(
            api_key="", model="deepseek-v4-flash", api_type="deepseek",
        )
    provider_config = config.providers[config.default_provider]
    api_type = getattr(provider_config, "api_type", "deepseek")
    if api_type == "deepseek":
        return DeepSeekProvider(provider_config.model_dump())
    return OpenAIProvider(provider_config.model_dump())


def create_builtin_tools(sandbox, config: ZOUWUCODEConfig = None) -> list[BaseTool]:
    """The standard builtin toolset shared by every UI.

    ``python_exec`` (CodeAct action surface) is included when config is
    provided and sandbox shell access is allowed — it can do what bash +
    files can do, so it follows the same permission posture.
    """
    tools = [
        ReadTool(sandbox),
        WriteTool(sandbox),
        EditTool(sandbox),
        LsTool(sandbox),
        GlobTool(sandbox),
        ShellTool(sandbox),
        GitTool(sandbox),
        WebSearchTool(sandbox),
        WebFetchTool(sandbox),
    ]
    if config is not None and getattr(config.sandbox, "allow_shell", True):
        tools.append(PythonExecTool(sandbox))
    return tools


def build_agent_engine(config: ZOUWUCODEConfig, sandbox_root, *,
                       mode: str = "yolo", with_subagents: bool = True):
    """Wire a complete agent stack (engine + registry + coordinator).

    Shared by the dev pipeline and the eval runner so both get identical
    safety wiring (yolo mode, sandbox rooted at the given directory,
    sub-agent task tool). Returns (engine, tools).
    """
    from .engine.loop import EngineLoop
    from .tools.registry import ToolRegistry
    from .sandbox.permission import PermissionManager
    from .agent.coordinator import AgentCoordinator
    from .agent.subagent import SubAgentManager
    from pathlib import Path

    provider = create_provider(config)
    engine = EngineLoop(config, provider)
    engine.set_mode(mode)
    sandbox = PermissionManager(config.sandbox)
    sandbox.set_workspace(Path(sandbox_root))
    tools = ToolRegistry()
    tools.register_all(create_builtin_tools(sandbox, config))
    coordinator = AgentCoordinator(config, engine, tools)
    engine.set_tool_executor(coordinator.execute_tool)
    if with_subagents:
        manager = SubAgentManager(config, provider, coordinator)
        manager.bind_main_engine(engine)
        from .tools.agent_tools import TaskTool
        tools.register(TaskTool(manager))
    return engine, tools
