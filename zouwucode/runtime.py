import logging

logger = logging.getLogger("zouwucode.runtime")

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
from .tools.repo_map_tool import RepoMapTool


def ensure_provider_config(config: ZOUWUCODEConfig, provider_key: str) -> ProviderConfig:
    """Guarantee ``provider_key`` exists in ``config.providers`` and return it.

    When no config.yaml is present (fresh install, CI), ``providers`` is an
    empty dict — CLI/TUI overrides for --model/--api-key must still land
    somewhere instead of being silently dropped (the v1.7.0 CI-red bug).
    Inference mirrors create_provider_for: openai-ish keys get the OpenAI
    defaults, everything else DeepSeek.
    """
    if provider_key not in config.providers:
        name = (provider_key or "").lower()
        api_type = "openai" if ("openai" in name or "gpt" in name) else "deepseek"
        model = "gpt-4o" if api_type == "openai" else "deepseek-flash"
        config.providers[provider_key] = ProviderConfig(
            api_key="", model=model, api_type=api_type,
        )
    return config.providers[provider_key]


def create_provider_for(config: ZOUWUCODEConfig, provider_key: str) -> BaseProvider:
    """Build the provider named ``provider_key`` (defaults injected if absent).

    Generalises ``create_provider`` so routing roles (oracle / worker) can name
    ANY key in ``providers:``, not only the default one.
    """
    provider_config = ensure_provider_config(config, provider_key)
    api_type = getattr(provider_config, "api_type", "deepseek")
    if api_type == "deepseek":
        return DeepSeekProvider(provider_config.model_dump())
    return OpenAIProvider(provider_config.model_dump())


def create_provider(config: ZOUWUCODEConfig) -> BaseProvider:
    """Build the configured DEFAULT LLM provider (defaults injected if missing)."""
    return create_provider_for(config, config.default_provider)


class ProviderRouter:
    """Lazily builds and caches providers for routing roles.

    The main engine stays single-provider by design: switching models mid
    conversation would invalidate the append-only prefix cache. Routing only
    selects a DIFFERENT provider for well-defined roles:

    - ``worker`` : the provider handed to sub-agents (the ``task`` tool).
    - ``oracle`` : a read-only second opinion that never touches the workspace.

    Role keys come from ``router.worker_provider`` / ``router.oracle_provider``
    (values are keys in ``providers:``); empty means "use the main provider".
    """

    def __init__(self, config: ZOUWUCODEConfig):
        self.config = config
        self._cache: dict = {}

    def get(self, provider_key: str):
        """Return (and cache) the provider for ``provider_key``; None if empty."""
        if not provider_key:
            return None
        if provider_key not in self._cache:
            self._cache[provider_key] = create_provider_for(self.config, provider_key)
        return self._cache[provider_key]

    def worker_provider(self, primary: BaseProvider) -> BaseProvider:
        r = getattr(self.config, "router", None)
        if r and getattr(r, "enabled", False) and r.worker_provider:
            return self.get(r.worker_provider) or primary
        return primary

    def oracle_provider(self, primary: BaseProvider = None):
        r = getattr(self.config, "router", None)
        if r and getattr(r, "enabled", False) and r.oracle_provider:
            return self.get(r.oracle_provider)
        return None


def _make_container(sandbox, config):
    """Build a ContainerSandbox when `sandbox.container` is enabled, else None.

    The container's workspace is the sandbox root, so the same directory the
    file tools are jailed to is the only thing mounted into the container.
    """
    if config is None or not getattr(config.sandbox, "container", False):
        return None
    try:
        from .sandbox.container import ContainerSandbox
        root = getattr(sandbox, "_workspace_root", None) or None
        return ContainerSandbox(
            workspace=root,
            image=getattr(config.sandbox, "container_image", "python:3.12-slim"),
            runtime=getattr(config.sandbox, "container_runtime", ""),
            network=getattr(config.sandbox, "container_network", False),
            mount=getattr(config.sandbox, "container_mount", "/workspace"),
        )
    except Exception as exc:  # noqa: BLE001 — never block tool creation
        logger.warning("container sandbox unavailable: %s", exc)
        return None


def create_builtin_tools(sandbox, config: ZOUWUCODEConfig = None) -> list[BaseTool]:
    """The standard builtin toolset shared by every UI.

    ``python_exec`` (CodeAct action surface) is included when config is
    provided and sandbox shell access is allowed — it can do what bash +
    files can do, so it follows the same permission posture.
    """
    # sandbox.allow_file_write used to be declared but never read; it now
    # gates write/edit at construction time (create_builtin_tools is THE
    # shared builtin toolset for CLI/TUI/Web/dev/eval).
    allow_write = True
    if config is not None:
        allow_write = getattr(config.sandbox, "allow_file_write", True)
    tools = [
        ReadTool(sandbox),
        WriteTool(sandbox, allow_write=allow_write),
        EditTool(sandbox, allow_write=allow_write),
        LsTool(sandbox),
        GlobTool(sandbox),
        ShellTool(sandbox, container=_make_container(sandbox, config)),
        GitTool(sandbox),
        WebSearchTool(sandbox),
        WebFetchTool(sandbox),
        RepoMapTool(sandbox),
    ]
    if config is not None and getattr(config.sandbox, "allow_shell", True):
        tools.append(PythonExecTool(sandbox))
    return tools


def create_subagents_and_extensions(config, provider, coordinator, engine, tools,
                                    workspace_root=None):
    """Wire the sub-agent system + extension host (shared by CLI and TUI).

    Both UI apps carried byte-identical copies of this wiring (which had
    started to drift); it lives here so the three frontends stay in sync.
    Returns (subagent_manager, extension_host, router).
    """
    from .agent.subagent import SubAgentManager
    from .tools.agent_tools import TaskTool
    from .extensions import ExtensionHost, LspExtension, McpExtension
    from .tools.oracle_tool import OracleTool

    # Model routing: sub-agents may run on a cheaper "worker" provider, and an
    # optional read-only "oracle" provider is exposed to the model as a tool.
    router = ProviderRouter(config)
    manager = SubAgentManager(config, router.worker_provider(provider), coordinator,
                              workspace_root=workspace_root)
    manager.bind_main_engine(engine)
    tools.register(TaskTool(manager))

    oracle = router.oracle_provider(provider)
    if oracle is not None:
        r = config.router
        tools.register(OracleTool(oracle, max_tokens=r.oracle_max_tokens,
                                  temperature=r.oracle_temperature))

    host = ExtensionHost()
    host.register(McpExtension(config.extensions.mcp_servers))
    host.register(LspExtension(config.extensions.lsp_enabled))
    return manager, host, router


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
        from .tools.agent_tools import TaskTool
        from .tools.oracle_tool import OracleTool

        router = ProviderRouter(config)
        manager = SubAgentManager(config, router.worker_provider(provider), coordinator,
                                  workspace_root=sandbox_root)
        manager.bind_main_engine(engine)
        tools.register(TaskTool(manager))
        oracle = router.oracle_provider(provider)
        if oracle is not None:
            r = config.router
            tools.register(OracleTool(oracle, max_tokens=r.oracle_max_tokens,
                                      temperature=r.oracle_temperature))
    return engine, tools
