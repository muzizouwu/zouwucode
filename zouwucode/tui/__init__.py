"""Terminal User Interface — CLI and Textual-based TUI.

Three interfaces:
- CLI: Interactive command-line loop (ZOUWUCODEApp.run_interactive)
- TUI: Full-featured Textual terminal UI (ZOUWUCODETUI)
- Web UI: Browser-based local HTTP server (WebUIServer)

``textual`` is an optional extra: the import below is guarded so a base
install (``pip install zouwucode``) still runs ``--version``, the CLI and
``--web``. Only ``--tui`` needs it, and it fails with an actionable hint.
"""

import sys

from ..application import ZOUWUCODEApp

_TEXTUAL_HINT = (
    "Textual TUI 需要额外依赖，当前环境未安装 textual。\n"
    "  请先执行：pip install \"zouwucode[tui]\"\n"
    "  （从源码安装时用：pip install -e \".[tui]\"）\n"
    "  然后重新运行：zouwucode --tui"
)

try:
    from .textual_app import ZOUWUCODETUI, run_tui
except ImportError as _textual_error:  # pragma: no cover - depends on env
    ZOUWUCODETUI = None
    _TEXTUAL_IMPORT_ERROR = _textual_error

    def run_tui(*_args, **_kwargs):
        """Placeholder used when the optional ``textual`` extra is missing."""
        print(_TEXTUAL_HINT, file=sys.stderr)
        print(f"  底层错误：{_TEXTUAL_IMPORT_ERROR}", file=sys.stderr)
        return 2


__all__ = ["ZOUWUCODEApp", "ZOUWUCODETUI", "run_tui"]
