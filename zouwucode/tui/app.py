"""Compatibility shim — the application core moved to ``zouwucode.application``.

Kept so ``from zouwucode.tui.app import ZOUWUCODEApp`` keeps working. New
code should import from :mod:`zouwucode.application`.
"""

from ..application import ZOUWUCODEApp  # noqa: F401
