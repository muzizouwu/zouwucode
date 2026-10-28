"""DeepSeek Harness (dsh) integration — drive the official harness from zouwucode.

DeepSeek Harness is DeepSeek's open-source agent harness (``dsh``): a Cordis
plugin kernel where every capability (models, tools, skills, sessions,
sandboxes, storage, loops, UI) is a swappable plugin. It ships an official
Python SDK that launches the bundled runtime and speaks newline-delimited
JSON-RPC over stdio.

This module is a deliberately thin, OPTIONAL adapter:

- zouwucode keeps owning its own engine — nothing changes unless the user
  explicitly asks for ``zouwucode harness …``.
- ``deepseek-harness-sdk`` is never imported at module load, so a plain
  install of zouwucode has zero new dependencies.
- Every failure mode (SDK missing, runtime/boot failure, provider not
  registered, bad DSH home) surfaces as an actionable message instead of a
  raw traceback — the SDK is a developer preview and will keep changing.
"""

import importlib.util
import inspect
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..config import ZOUWUCODEConfig
from ..errors import describe_exc

logger = logging.getLogger("zouwucode.harness")

SDK_MODULE = "deepseek_harness"
INSTALL_HINT = "python -m pip install deepseek-harness-sdk"

# dsh's default composition registers one provider route. zouwucode provider
# api_types map onto it; anything else needs an explicit --provider (the route
# must exist in the Cordis composition) or a custom patch layer.
_DSH_PROVIDER_BY_API_TYPE = {"deepseek": "deepseek-official"}

# Model id the shipped dsh profile resolves out of the box. The user's own
# model (config providers.<key>.model) wins when set, because they may run a
# composition that knows it.
_DEFAULT_DSH_MODEL = "deepseek-v4-flash"


class HarnessUnavailable(RuntimeError):
    """The DeepSeek Harness SDK/runtime cannot be used on this machine."""


class HarnessError(RuntimeError):
    """The harness ran but the turn failed."""


@dataclass(frozen=True)
class HarnessSettings:
    """Everything the SDK needs, resolved from zouwucode config + overrides."""

    dsh_home: Path
    cwd: Path
    provider: str
    model: str
    reasoning_effort: str
    max_tokens: int
    profile: str
    initialize_timeout_seconds: float
    request_timeout_seconds: float
    patches: tuple[str, ...]
    api_key: str
    base_url: str

    def sdk_kwargs(self) -> dict:
        """Keyword arguments for ``deepseek_harness.DeepSeekHarness``."""
        kwargs = {
            "dsh_home": str(self.dsh_home),
            "cwd": str(self.cwd),
            "provider": self.provider,
            "model": self.model,
            "profile": self.profile,
            "initialize_timeout_seconds": self.initialize_timeout_seconds,
        }
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if self.max_tokens and self.max_tokens > 0:
            kwargs["max_tokens"] = self.max_tokens
        if self.request_timeout_seconds and self.request_timeout_seconds > 0:
            kwargs["request_timeout_seconds"] = self.request_timeout_seconds
        if self.patches:
            kwargs["patches"] = tuple(self.patches)
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return kwargs


@dataclass(frozen=True)
class HarnessResult:
    """One harness activity interval, mapped to plain Python types."""

    session_id: str
    text: str
    finish_reason: Optional[str]
    event_count: int
    notification_count: int


def sdk_installed() -> bool:
    """True when the optional ``deepseek-harness-sdk`` is importable."""
    try:
        return importlib.util.find_spec(SDK_MODULE) is not None
    except (ImportError, ValueError):  # broken/partial install
        return False


def resolve_settings(
    config: ZOUWUCODEConfig,
    *,
    cwd: Optional[Path] = None,
    dsh_home: Optional[Path] = None,
    profile: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    max_tokens: Optional[int] = None,
    request_timeout_seconds: Optional[float] = None,
    patches: Optional[tuple[str, ...]] = None,
    base_url: Optional[str] = None,
) -> HarnessSettings:
    """Resolve SDK settings from zouwucode config, with CLI overrides winning.

    Raises ``ValueError`` with an actionable message when the combination is
    impossible (e.g. an OpenAI provider that dsh's default composition has no
    route for) — failing here beats a cryptic boot error from the SDK.
    """
    hcfg = getattr(config, "harness", None)
    workspace = Path(cwd or Path.cwd()).resolve()

    home = Path(dsh_home) if dsh_home else None
    if home is None and hcfg is not None and hcfg.dsh_home:
        home = Path(hcfg.dsh_home)
    if home is None:
        data_dir = Path(getattr(config, "data_dir", "") or Path.cwd())
        home = data_dir / "dsh_home"
    home = home.resolve()

    provider_key = getattr(config, "default_provider", "deepseek") or "deepseek"
    prov = (config.providers or {}).get(provider_key)
    api_type = (getattr(prov, "api_type", "") or "deepseek") if prov else "deepseek"

    chosen_provider = (
        provider
        or (getattr(hcfg, "provider", "") if hcfg is not None else "")
        or _DSH_PROVIDER_BY_API_TYPE.get(api_type, "")
    )
    if not chosen_provider:
        raise ValueError(
            f"DeepSeek Harness 的默认组合只注册了 deepseek-official；当前 provider "
            f"'{provider_key}' 的 api_type='{api_type}' 无法直接映射。"
            f"请用 --provider 指定 DSH 组合中已注册的路由，或用 harness.patches "
            f"挂载自定义 provider 插件（如 llm-pi-ai）。")

    chosen_model = (
        model
        or (getattr(hcfg, "model", "") if hcfg is not None else "")
        or (getattr(prov, "model", "") if prov else "")
        or _DEFAULT_DSH_MODEL
    )
    effort = (
        reasoning_effort
        if reasoning_effort is not None
        else (getattr(hcfg, "reasoning_effort", "") if hcfg is not None
              else "")
    )
    # NOTE: deliberately NOT inheriting zouwucode's ``reasoning_intensity``.
    # dsh validates the effort against the selected adapter and rejects values
    # it does not know (e.g. deepseek-official refuses "medium"), so the two
    # runtimes' tiers are not interchangeable. Omission means "model default".

    chosen_max_tokens = (
        max_tokens if max_tokens is not None
        else (getattr(hcfg, "max_tokens", 49152) if hcfg is not None else 49152)
    )
    init_timeout = (
        getattr(hcfg, "initialize_timeout_seconds", 30.0) if hcfg is not None
        else 30.0)
    chosen_timeout = (
        request_timeout_seconds if request_timeout_seconds is not None
        else (getattr(hcfg, "request_timeout_seconds", 0.0) if hcfg is not None
              else 0.0))
    chosen_patches = (
        patches if patches is not None
        else tuple(getattr(hcfg, "patches", ()) or ()))

    chosen_base_url = (
        base_url if base_url is not None
        else (getattr(hcfg, "base_url", "") if hcfg is not None else ""))
    # Deliberately NOT inheriting providers.<key>.base_url: that endpoint is
    # OpenAI-compatible, while dsh's own providers speak a different API shape
    # (forwarding it produced an HTTP 404 on the first turn).

    return HarnessSettings(
        dsh_home=home,
        cwd=workspace,
        provider=chosen_provider,
        model=chosen_model,
        reasoning_effort=effort,
        max_tokens=int(chosen_max_tokens),
        profile=profile or (getattr(hcfg, "profile", "sdk") if hcfg is not None
                            else "sdk"),
        initialize_timeout_seconds=float(init_timeout),
        request_timeout_seconds=float(chosen_timeout),
        patches=tuple(str(p) for p in chosen_patches),
        api_key=(getattr(prov, "api_key", "") if prov else "") or "",
        base_url=chosen_base_url,
    )


def _turn_end_reason(events) -> str:
    """Best-effort failure detail from the last ``turn/end`` event.

    The SDK surfaces ``finish_reason == "error"`` without a message, and the
    real cause (provider rejection, auth, tool failure) lives in the event's
    ``data.reason``. Without this the user only sees "error" — a preview
    runtime must at least explain itself.
    """
    for event in reversed(list(events or ())):
        if not isinstance(event, dict) or event.get("type") != "turn/end":
            continue
        data = event.get("data") or {}
        reason = data.get("reason") or {}
        if isinstance(reason, dict):
            kind = reason.get("kind") or reason.get("type") or ""
            detail = reason.get("message") or reason.get("error") or ""
            if kind and detail:
                return f"{kind} — {detail}"
            if kind or detail:
                return str(kind or detail)
        elif reason:
            return str(reason)
        return ""
    return ""


def _platform_hint(detail: str) -> str:
    """Extra guidance for known upstream runtime defects (preview era)."""
    text = (detail or "").lower()
    if "koffi" in text or "specified module could not be found" in text:
        return (
            "\n[hint] 这是 dsh 运行时（deepseek-harness-runtime-bin 0.2.1rc2）"
            "在 Windows 上的原生模块打包缺陷，不是 zouwucode 的问题。"
            "可先用 `--profile sdk-minimal` 运行，或升级到修复后的 SDK。")
    if "does not support reasoning effort" in text:
        return (
            "\n[hint] dsh 的 provider 适配器只接受它认识的 effort 档位"
            "（例如 deepseek-official 不接受 medium）。去掉 --reasoning，"
            "或改用 low/max。")
    return ""


def _load_sdk():
    """Import the optional SDK, turning absence into a usable message."""
    if not sdk_installed():
        raise HarnessUnavailable(
            "DeepSeek Harness SDK 未安装。安装后再试：\n"
            f"  {INSTALL_HINT}\n"
            "（该包会同时安装对应平台的 dsh 运行时 wheel，无需系统 Node.js）")
    try:
        import deepseek_harness  # noqa: PLC0415 — optional dependency
    except Exception as exc:  # noqa: BLE001 — partial/broken install
        raise HarnessUnavailable(
            f"DeepSeek Harness SDK 导入失败：{describe_exc(exc)}。"
            f"请重新安装：{INSTALL_HINT}") from exc
    return deepseek_harness


class HarnessRunner:
    """Thin wrapper that runs one prompt through the official harness.

    A runner is cheap to construct and reuses the SDK's runtime while the
    session id stays the same (the SDK keeps the runtime alive between runs).
    """

    def __init__(self, settings: HarnessSettings):
        self.settings = settings
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        sdk = _load_sdk()
        cls = getattr(sdk, "DeepSeekHarness", None)
        if cls is None:  # pragma: no cover — SDK contract changed
            raise HarnessUnavailable(
                "deepseek-harness-sdk 中没有 DeepSeekHarness 入口，"
                "请升级/重装该包。")
        try:
            self.settings.dsh_home.mkdir(parents=True, exist_ok=True)
            self._client = cls(**self.settings.sdk_kwargs())
        except TypeError as exc:
            raise HarnessUnavailable(
                "deepseek-harness-sdk 版本与当前参数不兼容 "
                f"({describe_exc(exc)})；请升级 SDK 或去掉不支持的参数。") from exc
        except Exception as exc:  # boot failure (profile/config/runtime)
            raise HarnessError(
                f"DeepSeek Harness 启动失败：{describe_exc(exc)}。"
                f"可用 `dsh --profile {self.settings.profile} "
                f"--dump-default-config` 自检该 DSH_HOME。") from exc
        return self._client

    def run(
        self,
        prompt: str,
        *,
        session_id: Optional[str] = None,
        on_notification: Optional[Callable[[object], None]] = None,
    ) -> HarnessResult:
        """Run one prompt and return the mapped result.

        ``on_notification`` (when the SDK supports it) receives sub-agent and
        root notifications in wire order, so a UI can stream progress.
        """
        if not (prompt or "").strip():
            raise ValueError("prompt must not be empty")
        client = self._ensure_client()
        kwargs = {}
        if session_id:
            kwargs["session_id"] = session_id
        supports_notify = False
        if on_notification is not None:
            try:
                supports_notify = "on_notification" in inspect.signature(
                    client.run).parameters
            except (TypeError, ValueError):  # builtins / no signature
                supports_notify = False
            if supports_notify:
                kwargs["on_notification"] = on_notification
        try:
            raw = client.run(prompt, **kwargs)
        except Exception as exc:  # noqa: BLE001 — surface as harness failure
            raise HarnessError(
                f"DeepSeek Harness 执行失败：{describe_exc(exc)}") from exc

        text = getattr(raw, "final_response", "") or ""
        reason = getattr(raw, "finish_reason", None)
        events = getattr(raw, "events", ()) or ()
        notifications = getattr(raw, "notifications", ()) or ()
        if reason == "error":
            detail = _turn_end_reason(events)
            detail = detail or text or "无输出"
            raise HarnessError(
                "DeepSeek Harness 以 error 结束本轮"
                f"（会话 {session_id or '<new>'}）：{detail}。"
                "可用 --verbose 查看完整事件流。" + _platform_hint(str(detail)))
        return HarnessResult(
            session_id=getattr(raw, "session_id", session_id or "") or "",
            text=text,
            finish_reason=reason,
            event_count=len(events),
            notification_count=len(notifications),
        )

    def close(self) -> None:
        """Release the runtime subprocess (safe to call more than once)."""
        client, self._client = self._client, None
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:  # noqa: BLE001 — shutdown is best effort
                logger.warning("harness close failed: %s", describe_exc(exc))
