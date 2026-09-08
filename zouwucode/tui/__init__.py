"""Terminal User Interface — CLI and Textual-based TUI.

Three interfaces:
- CLI: Interactive command-line loop (ZOUWUCODEApp.run_interactive)
- TUI: Full-featured Textual terminal UI (ZOUWUCODETUI)
- Web UI: Browser-based local HTTP server (WebUIServer)
"""

from .app import ZOUWUCODEApp
from .textual_app import ZOUWUCODETUI, run_tui

__all__ = ["ZOUWUCODEApp", "ZOUWUCODETUI", "run_tui"]