"""Configuration management for ZOUWUCODE."""

import json
import yaml
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field


# ── Portable data directory ──────────────────────────────────────────────────
# Use a sibling folder `zouwucode_data` next to the executable/bundle so it
# never touches the system drive or user home unless explicitly told.
def _default_data_dir() -> Path:
    """Return the data directory next to the bundled executable or CWD."""
    # When frozen by PyInstaller, sys.executable is the .exe path.
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent.resolve()
    else:
        base = Path.cwd().resolve()
    return base / "zouwucode_data"


import sys


class ProviderConfig(BaseModel):
    """Configuration for a single LLM provider."""

    api_key: str = ""
    base_url: str = ""
    model: str = ""
    api_type: str = "deepseek"  # deepseek | openai | custom


class EngineConfig(BaseModel):
    """Engine loop safety limits — guards against infinite tool loops."""

    max_tool_rounds: int = 25              # 单次任务最多工具轮数（防止模型反复调用工具的死循环）
    turn_timeout_seconds: float = 300.0    # 单次 LLM 流式请求超时（秒）
    task_timeout_seconds: float = 1800.0   # 单次任务（run）总耗时上限（秒）
    max_consecutive_tool_errors: int = 3   # 工具连续失败次数达到该值即终止任务
    # 瞬时故障（429/5xx/网络错误）的指数退避重试；非瞬时错误（401/400）不重试
    max_llm_retries: int = 2               # 单次 LLM 请求失败后的最大重试次数（0=关闭）
    retry_base_delay_seconds: float = 1.0  # 退避基数：第 n 次重试等待 base * 2^(n-1) 秒


class CacheConfig(BaseModel):
    """Prefix-cache tuning parameters."""

    enabled: bool = True
    max_prefix_tokens: int = 128_000
    append_only: bool = True
    stats_window: int = 50  # number of recent turns for cache-hit stats


class SandboxConfig(BaseModel):
    """Permission / sandbox settings."""

    enabled: bool = True
    default_mode: str = "agent"  # plan | agent | yolo
    allow_shell: bool = True
    allow_file_write: bool = True
    allow_network: bool = True
    allow_git: bool = True
    allowed_paths: list[str] = Field(default_factory=lambda: ["."])


class SessionConfig(BaseModel):
    """Session persistence settings."""

    save_enabled: bool = True
    auto_save_interval: int = 60  # seconds
    max_sessions: int = 50
    rollback_enabled: bool = True


class HelloMyZouwucodeConfig(BaseModel):
    """hello-my-zouwucode module settings (multi-agent orchestration)."""

    enabled: bool = True                 # master switch for the module
    state_dir: str = ".hello-my-zouwucode"  # state dir (boulder/plans/notepads)
    default_category: str = "deep"       # category used when none is specified
    max_review_rounds: int = 2           # Momus approval loop cap (0 = unlimited)
    interactive_planning: bool = True    # allow Prometheus interview mode


class SubAgentConfig(BaseModel):
    """Sub-agent system settings."""

    max_agents: int = 8                  # 并行子 Agent 数量上限
    default_timeout: float = 600.0       # 单个子 Agent 任务默认超时（秒）


class McpServerConfig(BaseModel):
    """One MCP server connection (reserved — inactive until configured)."""

    name: str                            # 服务器名（工具将注册为 name__tool）
    command: str                         # stdio 启动命令
    args: list[str] = Field(default_factory=list)


class ExtensionsConfig(BaseModel):
    """Extension layer settings — MCP / LSP integration space (reserved).

    Both are inactive by default: the ExtensionHost and adapters are wired,
    but nothing connects until explicitly configured.
    """

    mcp_servers: list[McpServerConfig] = Field(default_factory=list)
    lsp_enabled: bool = False            # 启用后注册 check_diagnostics 工具


class ZOUWUCODEConfig(BaseModel):
    """Root configuration model."""

    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    default_provider: str = "deepseek"
    engine: EngineConfig = Field(default_factory=EngineConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    subagent: SubAgentConfig = Field(default_factory=SubAgentConfig)
    extensions: ExtensionsConfig = Field(default_factory=ExtensionsConfig)
    hello_my_zouwucode: HelloMyZouwucodeConfig = Field(default_factory=HelloMyZouwucodeConfig)
    reasoning_intensity: str = "medium"  # low | medium | max
    show_thinking: bool = True           # 流式显示思考过程（/thinking 三端切换，默认开）
    log_level: str = "info"              # 文件日志级别：debug | info | warning | error
    data_dir: str = ""
    theme: str = "dark"
    language: str = "zh"

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "ZOUWUCODEConfig":
        """Load config from YAML/JSON file, or create default."""
        if path is None:
            # Priority 1: current working directory
            cwd_path = Path.cwd() / "config.yaml"
            # Priority 2: sibling folder `zouwucode_data` next to CWD
            data_dir = Path(_default_data_dir())
            data_dir_path = data_dir / "config.yaml"
            # Priority 3: original install directory (for pip -e . installed packages)
            install_path = None
            try:
                # Find the root package directory
                import zouwucode
                pkg_root = Path(zouwucode.__file__).parent.parent.resolve()
                install_candidate = pkg_root / "config.yaml"
                if install_candidate.exists():
                    install_path = install_candidate
            except Exception:
                pass

            if cwd_path.exists():
                path = cwd_path
            elif data_dir_path.exists():
                path = data_dir_path
            elif install_path:
                path = install_path
            else:
                # No config found anywhere — return defaults
                cfg = cls()
                cfg.data_dir = str(_default_data_dir())
                return cfg

        if path.exists():
            raw = path.read_text(encoding="utf-8")
            if path.suffix in (".yaml", ".yml"):
                data = yaml.safe_load(raw)
            else:
                data = json.loads(raw)
            return cls(**data)

        # Return defaults
        cfg = cls()
        cfg.data_dir = str(_default_data_dir())
        return cfg

    def save(self, path: Optional[Path] = None) -> None:
        """Persist config to disk."""
        if path is None:
            path = Path(self.data_dir) / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.model_dump()
        path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")