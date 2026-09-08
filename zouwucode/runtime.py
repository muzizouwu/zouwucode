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


def create_builtin_tools(sandbox) -> list[BaseTool]:
    """The standard builtin toolset shared by every UI."""
    return [
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
