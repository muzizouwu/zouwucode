"""ZOUWUCODE module system — extendable modules loaded by the core framework.

A module is a self-contained package that can hook into the core via:
- ``handle_message``  — intercept a user message (e.g. "ultrawork ..." prefix)
- ``handle_command``  — provide slash commands (e.g. ``/hello-plan``)
- ``on_event``        — receive orchestration progress events for rendering

Modules are loaded on demand by ``ModuleManager`` (see config ``hello_my_zouwucode``).
This mirrors how plugins extend OpenCode while keeping ZOUWUCODE as the host.
"""

from .manager import ModuleManager

__all__ = ["ModuleManager"]
