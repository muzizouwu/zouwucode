"""DeepSeek Harness (dsh) integration layer.

Optional and off by default: see ``zouwucode/harness/deepseek.py`` for the
adapter and ``docs/deepseek-harness接入.md`` for the user guide.
"""

from .deepseek import (
    INSTALL_HINT,
    HarnessError,
    HarnessResult,
    HarnessRunner,
    HarnessSettings,
    HarnessUnavailable,
    resolve_settings,
    sdk_installed,
)

__all__ = [
    "INSTALL_HINT",
    "HarnessError",
    "HarnessResult",
    "HarnessRunner",
    "HarnessSettings",
    "HarnessUnavailable",
    "resolve_settings",
    "sdk_installed",
]
