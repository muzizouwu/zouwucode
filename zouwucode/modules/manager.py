"""ModuleManager — loads and dispatches to ZOUWUCODE extension modules.

The core (CLI / TUI / Web) never hard-codes a module's internals. Instead it
asks the manager to dispatch messages / commands / events; each loaded module
decides whether it handles them and returns a renderable result dict::

    {
        "title":   str,   # short heading, e.g. "Ultrawork"
        "content": str,   # multi-line main output
        "details": list,  # optional detail lines
    }

If a module returns ``None`` the core falls through to its built-in handling.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..errors import describe_exc

logger = logging.getLogger("zouwucode.modules")


class ModuleManager:
    """Registry + dispatcher for loadable ZOUWUCODE modules."""

    def __init__(self, config=None, engine=None):
        self.config = config
        self.engine = engine
        self._modules: dict[str, object] = {}

    # ── Registration ─────────────────────────────────────────────────────

    def register(self, module) -> None:
        """Register a module instance (must expose name/handle_* methods)."""
        name = getattr(module, "name", None)
        if not name:
            raise ValueError("module must define a `name` attribute")
        self._modules[name] = module

    def unregister(self, name: str) -> bool:
        """Unload a module by name. Returns True if it was loaded."""
        return self._modules.pop(name, None) is not None

    def is_loaded(self, name: str) -> bool:
        return name in self._modules

    def loaded(self) -> list[str]:
        """Names of currently loaded modules."""
        return list(self._modules)

    def module(self, name: str):
        """Get a loaded module instance by name."""
        return self._modules.get(name)

    def status(self) -> list[dict]:
        """Module inventory for status UIs."""
        return [
            {
                "name": m.name,
                "version": getattr(m, "version", ""),
                "description": getattr(m, "description", ""),
                "commands": getattr(m, "commands", []),
            }
            for m in self._modules.values()
        ]

    # ── Dispatch ─────────────────────────────────────────────────────────

    async def dispatch_message(self, text: str) -> Optional[dict]:
        """Let modules intercept a user message.

        Returns a renderable result dict, or None if no module handled it.
        """
        for name, module in self._modules.items():
            handler = getattr(module, "handle_message", None)
            if handler is None:
                continue
            try:
                result = await handler(text)
            except Exception as exc:
                # A broken module must not swallow the user's message, but its
                # failure must be visible — silently skipping made third-party
                # module bugs undiagnosable.
                logger.warning("module '%s' handle_message failed: %s",
                               name, describe_exc(exc))
                continue
            if result is not None:
                return result
        return None

    async def dispatch_command(
        self, command: str, arg: str = "", context: Optional[dict] = None
    ) -> Optional[dict]:
        """Let modules handle a slash command.

        Returns a renderable result dict, or None if no module handled it.
        """
        for name, module in self._modules.items():
            handler = getattr(module, "handle_command", None)
            if handler is None:
                continue
            try:
                result = await handler(command, arg, context or {})
            except Exception as exc:
                logger.warning("module '%s' handle_command failed: %s",
                               name, describe_exc(exc))
                continue
            if result is not None:
                return result
        return None

    def dispatch_event(self, etype: str, message: str, payload: dict) -> None:
        """Broadcast an orchestration event to all loaded modules."""
        for name, module in self._modules.items():
            handler = getattr(module, "on_event", None)
            if handler is None:
                continue
            try:
                handler(etype, message, payload)
            except Exception as exc:
                logger.warning("module '%s' on_event failed: %s",
                               name, describe_exc(exc))
                continue
