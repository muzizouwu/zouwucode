"""Lifecycle hooks — deterministic automation around every tool call.

Modeled on Claude Code's PreToolUse/PostToolUse (the two highest-value
events of its 27-event hook system): shell commands registered at tool
lifecycle points, configured in ``extensions.hooks`` (empty by default,
zero overhead when unused).

- pre_tool:  runs before the tool executes. The hook can BLOCK the call:
  exit code != 0 (stderr as reason) or stdout JSON {"block": true,
  "reason": "..."} both veto the action and feed the reason back to the
  model as an error result — the model sees WHY and can adapt.
- post_tool: runs after execution (e.g. auto-format after edit). Output
  is logged; failures never corrupt the tool result.

Command templates support {tool}, {args}, {result} placeholders (filled
from the tool call; missing placeholders are left as-is). Each hook has a
10s timeout; a hung hook fails open (post) / closed (pre blocks with a
timeout reason) — never wedges the whole loop.
"""

import asyncio
import json
import logging
from typing import Optional

from ..engine.providers.base import ToolCall, ToolResult

logger = logging.getLogger("zouwucode.hooks")

_HOOK_TIMEOUT = 10.0


class HookRunner:
    """Runs configured lifecycle hooks for one tool call."""

    def __init__(self, hooks: list):
        # list[HookConfig]; kept simple — matching is a substring on tool name
        self._hooks = list(hooks or [])

    def _matching(self, event: str, tool_name: str) -> list:
        return [h for h in self._hooks
                if h.event == event
                and (not h.tool_pattern or h.tool_pattern in tool_name)]

    @staticmethod
    def _fill(template: str, tool_call: ToolCall,
              result: Optional[ToolResult] = None) -> str:
        mapping = {
            "tool": tool_call.name,
            "args": tool_call.arguments if isinstance(tool_call.arguments, str)
                    else json.dumps(tool_call.arguments, ensure_ascii=False),
        }
        if result is not None:
            mapping["result"] = (result.content or "")[:2000]
        try:
            return template.format(**mapping)
        except (KeyError, IndexError):
            return template  # unknown placeholder — run as authored

    async def _exec(self, command: str) -> tuple[int, str, str]:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(
                proc.communicate(), timeout=_HOOK_TIMEOUT)
            return (proc.returncode or 0,
                    out.decode("utf-8", "replace"),
                    err.decode("utf-8", "replace"))
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return -1, "", f"hook timed out after {_HOOK_TIMEOUT:.0f}s"
        except Exception as exc:  # noqa: BLE001 — hook infra errors are reasons
            return -1, "", f"hook error: {exc}"

    async def run_pre(self, tool_call: ToolCall) -> Optional[str]:
        """Returns a block-reason string if a pre_tool hook vetoes the call."""
        for h in self._matching("pre_tool", tool_call.name):
            rc, out, err = await self._exec(self._fill(h.command, tool_call))
            if rc == 0:
                # stdout may still carry an explicit {"block": true}
                try:
                    data = json.loads(out.strip()) if out.strip() else {}
                    if isinstance(data, dict) and data.get("block"):
                        reason = str(data.get("reason") or "blocked by hook")
                        logger.info("pre_tool hook blocked %s: %s",
                                    tool_call.name, reason)
                        return reason
                except json.JSONDecodeError:
                    pass
                continue
            reason = (err.strip() or out.strip() or
                      f"hook exit {rc}")[:500]
            logger.info("pre_tool hook blocked %s (exit %d): %s",
                        tool_call.name, rc, reason)
            return reason
        return None

    async def run_post(self, tool_call: ToolCall,
                       result: ToolResult) -> None:
        for h in self._matching("post_tool", tool_call.name):
            rc, out, err = await self._exec(
                self._fill(h.command, tool_call, result))
            if rc != 0:
                logger.warning("post_tool hook for %s failed (exit %d): %s",
                               tool_call.name, rc, (err or out)[:300])
