"""Main ZOUWUCODE application — wires all components together.

Integrates:
- Persistent project memory (cross-session state)
- Sliding window context manager (ultra-long context)
- Auto-dialogue compression (intelligent compression)
- CLI, TUI, and Web UI interfaces
- Quick launch support
"""
import logging

logger = logging.getLogger("zouwucode.tui.app")


import asyncio
import os
import sys
import time
import signal
from pathlib import Path
from typing import Optional

from .. import __version__
from ..config import ZOUWUCODEConfig
from ..logging_setup import setup_logging
from ..engine.loop import EngineLoop, TaskInterrupted
from ..runtime import (create_provider, create_builtin_tools,
                       create_subagents_and_extensions)
from ..tools.oracle_tool import ORACLE_SYSTEM
from ..tools.registry import ToolRegistry
from ..agent.coordinator import AgentCoordinator
from ..extensions import ExtensionContext
from ..context.memory import MemoryManager
from ..context.topics import TopicManager
from ..context.transcript import TranscriptManager
from ..context.compressor import DialogueCompressor
from ..context.sliding_window import ContextWindow
from ..sandbox.permission import PermissionManager
from ..session.manager import SessionManager
from ..share import export_session
from ..messaging import MessageBus, format_inbox, format_peers
from ..project_memory import ProjectMemory
from ..skills import RuleLoader, SkillsManager
from ..webui import WebUIServer


class ZOUWUCODEApp:
    """Main application class that wires all components together.

    Supports three interfaces:
    - CLI (Command Line Interface): Interactive terminal loop
    - Web UI (Browser Interface): Local HTTP server with HTML/JS frontend
    - Single message: One-shot message processing
    """

    APP_NAME = "ZOUWUCODE"
    APP_VERSION = __version__

    def __init__(self, config_path: Optional[Path] = None, skill_names: list[str] | None = None,
                 provider: str | None = None):
        self.config = ZOUWUCODEConfig.load(config_path)
        if provider:
            self.config.default_provider = provider
        self.data_dir = Path(self.config.data_dir)

        # ── Logging (rotating file under <data_dir>/logs/) ────────────────
        setup_logging(self.data_dir, self.config.log_level)

        # ── Project Memory (persistent, cross-session) ──────────────────────
        self.project_memory = ProjectMemory()
        self.project_memory.load_all()

        # ── LLM Provider ──────────────────────────────────────────────────
        self._provider = self._create_provider()

        # ── Engine ─────────────────────────────────────────────────────────
        self.engine = EngineLoop(self.config, self._provider)

        # ── Reasoning Intensity (applied to provider) ────────────────────────
        self.engine.set_reasoning_intensity(self.config.reasoning_intensity)

        # ── Sandbox ────────────────────────────────────────────────────────
        self.sandbox = PermissionManager(self.config.sandbox)
        self.sandbox.set_workspace(Path.cwd())  # scope file tools to the workspace

        # ── Tools ──────────────────────────────────────────────────────────
        self.tools = ToolRegistry()

        # ── Agent ──────────────────────────────────────────────────────────
        self.coordinator = AgentCoordinator(self.config, self.engine, self.tools)
        self.coordinator.project_memory = self.project_memory  # inject for web UI
        self.engine.set_tool_executor(self.coordinator.execute_tool)
        # Interactive approval state (agent mode): tools the user chose to
        # always allow for this session. The callback itself is installed by
        # run_interactive() — a headless run_single() must never block stdin.
        self._approved_tools: set = set()

        # ── Sub-agents & extensions (MCP/LSP reserved space) ───────────────
        self._setup_agents_and_extensions()

        # ── Tools registration (built-in + extension-contributed) ──────────
        self._setup_tools()

        # ── Context Management ─────────────────────────────────────────────
        workspace = Path.cwd().resolve()
        self.memory = MemoryManager(workspace)
        self.topics = TopicManager(workspace)
        self.transcripts = TranscriptManager(self.data_dir)

        # ── Context window (stats) ─────────────────────────────────────────
        self.compressor = DialogueCompressor(level="balanced")
        self.context_window = ContextWindow()
        self.context_window.set_compressor(self.compressor)
        self.compressor_level = "balanced"
        self.max_context_tokens = 1048576
        self.keep_turns = 50
        self._last_project_context = ""  # only re-send project memory when it changes

        # ── Session ────────────────────────────────────────────────────────
        self.sessions = SessionManager(self.config)
        # Cross-session messaging (local SQLite bus; /msg /inbox /peers).
        self._bus = self._init_message_bus()

        # ── Rules & Skills (.zouwucode/rules.md + .zouwucode/skills/) ──────
        self.rule_loader = RuleLoader(workspace)
        self.skills = SkillsManager(workspace)
        self._preload_skills = skill_names or []

        # ── Module system (loadable extensions) ───────────────────────────
        # ZOUWUCODE is the host framework; modules are loaded on demand.
        # hello-my-zouwucode is the ported multi-agent orchestration module.
        from ..modules.manager import ModuleManager

        self.modules = ModuleManager(self.config, self.engine)
        if self.config.hello_my_zouwucode.enabled:
            try:
                from hello_my_zouwucode.module import HelloMyZouwucodeModule

                self.modules.register(
                    HelloMyZouwucodeModule(
                        self.engine,
                        config=self.config,
                        on_event=self._on_module_event,
                        skills_manager=self.skills,
                    )
                )
            except ModuleNotFoundError:
                # The module ships next to the package; a missing import means
                # the install predates the packaging fix (or is a stale
                # editable install). Point at the one-step remedy.
                print("  [Warning] hello-my-zouwucode 未安装：请重新执行 "
                      "`pip install -e .`（或升级 zouwucode）后重启。")
            except Exception as e:  # module optional — never block startup
                print(f"  [Warning] hello-my-zouwucode module failed to load: {e}")

        # ── State ──────────────────────────────────────────────────────────
        self._current_mode = self.config.sandbox.default_mode
        self._running = False
        self._session_id: Optional[str] = None
        self._thinking_streaming = False  # set while streaming thinking deltas
        self._content_streaming = False   # set while streaming assistant text

        # ── Register signal handlers for graceful shutdown ─────────────────
        signal.signal(signal.SIGINT, self._signal_handler)

    def _create_provider(self):
        """Create the LLM provider based on configuration."""
        return create_provider(self.config)

    def _setup_tools(self):
        """Register all built-in tools.

        Extension-contributed tools (MCP/LSP) are registered by
        ExtensionHost.start() — which must run before freeze_session().
        """
        self.tools.register_all(create_builtin_tools(self.sandbox, self.config))

    def _setup_agents_and_extensions(self):
        """Wire the sub-agent system and the extension layer (MCP/LSP)."""
        self.subagent_manager, self.extensions, self.router = create_subagents_and_extensions(
            self.config, self._provider, self.coordinator, self.engine, self.tools)

    def _init_message_bus(self):
        """Wire the local cross-session message bus (best-effort; None on failure)."""
        mcfg = getattr(self.config, "messaging", None)
        if mcfg is None or not getattr(mcfg, "enabled", True):
            return None
        try:
            import uuid as _uuid
            name = (getattr(mcfg, "name", "") or "").strip()
            if not name:
                base = Path.cwd().name[:20] or "session"
                name = f"{base}-{_uuid.uuid4().hex[:4]}"
            bus = MessageBus(Path(self.data_dir) / "messages.db", agent=name)
            bus.register()
            return bus
        except Exception:  # never let messaging wiring break startup
            return None

    @property
    def bus(self):
        return getattr(self, "_bus", None)

    def share_current_session(self) -> dict:
        """Export the current session to <data_dir>/shares/ as Markdown."""
        sid = self.sessions.current_session_id
        data = self.sessions.get_session(sid) if sid else None
        if not data:
            data = {"session_id": sid or "session", "project_root": str(Path.cwd()),
                    "turns": self.sessions.current_log()}
        try:
            path = export_session(data, Path(self.data_dir) / "shares",
                                  session_id=data.get("session_id", ""))
            return {"ok": True, "path": str(path), "format": "markdown",
                    "turns": len(data.get("turns", []) or [])}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    # ── Mode Management ──────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._current_mode

    def set_mode(self, mode: str) -> None:
        """Switch between plan, agent, and yolo modes."""
        if mode in ("plan", "agent", "yolo"):
            self._current_mode = mode
            self.engine.set_mode(mode)

    @property
    def reasoning_intensity(self) -> str:
        return self.config.reasoning_intensity

    def set_reasoning_intensity(self, level: str) -> None:
        """Set reasoning effort level (low | medium | max)."""
        if level in ("low", "medium", "max"):
            self.config.reasoning_intensity = level
            self.engine.set_reasoning_intensity(level)

    # ── Session Lifecycle ────────────────────────────────────────────────────

    def switch_workspace(self, path: str) -> dict:
        """Re-root the agent at another folder (new session + new sandbox scope).

        Mid-process switching cannot re-freeze the engine's system prompt, so
        this does the honest subset: change cwd, re-scope the sandbox to the new
        folder, reload that project's memory, and start a fresh session whose
        turns are recorded against the new ``project_root``. The caller is told
        what changed so the UI can say "restart for full isolation".
        """
        from pathlib import Path as _Path

        try:
            target = _Path(str(path)).expanduser().resolve()
        except (OSError, RuntimeError):
            return {"ok": False, "error": "invalid path"}
        if not target.is_dir():
            return {"ok": False, "error": "not a directory"}
        previous = _Path.cwd().resolve()
        if target == previous:
            return {"ok": True, "workspace": str(target), "changed": False,
                    "session_id": self.sessions.current_session_id}

        import os as _os

        _os.chdir(target)
        try:
            self.sandbox.set_workspace(target)
        except Exception as exc:  # pragma: no cover — sandbox already configured
            logger.warning("sandbox re-scope failed: %s", exc)
        try:
            self.project_memory = ProjectMemory()
        except Exception as exc:  # pragma: no cover
            logger.warning("project memory reload failed: %s", exc)
        self.sessions = SessionManager(self.config)
        sid = self.sessions.start_session()
        return {"ok": True, "workspace": str(target), "changed": True,
                "previous": str(previous), "session_id": sid,
                "note": "新会话已开始；系统提示与项目记忆在下一次启动时完全生效"}

    def initialize_session(self, session_id: Optional[str] = None) -> str:
        """Initialize a session, resuming a previous one when asked.

        The resume intent comes from ``session_id`` or from the --resume /
        --session CLI flags (stored as self._resume_session). A resumed
        session reloads its turn log and replays it into the engine so the
        conversation actually continues — the PrefixCache alone only holds
        the system prompt.
        """
        requested = session_id or getattr(self, "_resume_session", None)
        if requested == "__latest__":
            latest = self.sessions.latest_session()
            requested = latest["id"] if latest else None
        resumed = bool(requested) and self.sessions.load_session(requested) is not None
        if requested and not resumed:
            print(f"  [Warning] session '{requested}' not found — "
                  f"starting a new session.")
        self._session_id = (requested if resumed
                            else self.sessions.start_session())

        # Preload skills specified via --skill CLI argument
        for name in self._preload_skills:
            try:
                self.skills.load(name)
            except FileNotFoundError as e:
                print(f"  [Warning] {e}")

        # Load project memory context
        project_context = self.project_memory.get_full_context()

        # Build system prompt with project memory
        system_prompt = self._build_system_prompt(project_context)

        # Freeze the system prompt and tool schemas for cache stability
        tool_schemas = self.tools.get_schemas()
        self.engine.freeze_session(system_prompt, tool_schemas)

        # Add to hot zone (always kept)
        self.context_window.add_to_hot([
            {"role": "system", "content": system_prompt},
        ])

        # Resume: replay the loaded history into the live engine. Without
        # this the session file would load fine while the model still saw
        # an empty conversation — "continue last task" would silently fail.
        if resumed:
            self._replayed = self.sessions.replay_history(
                self.sessions.current_log(), self.engine.cache,
                self.context_window)
            print(f"  [Resumed {self._session_id}] "
                  f"{self._replayed} messages replayed into context")

        # Baseline snapshot for workspace rollback (/rollback). A no-op when
        # rollback is disabled or git is unavailable.
        if getattr(self.sessions, "snapshotter", None) is not None:
            self.sessions.snapshot_workspace("session start")
            # Bind the baseline to message index 0 (new sessions only — a
            # resumed session restores its own checkpoints from disk).
            if not resumed:
                self.sessions.record_checkpoint(0)

        self.transcripts.start_session(self._session_id)
        return self._session_id

    def resume_session(self, session_id: str) -> list:
        """Load a saved session and replay it into the running engine.

        Powers the Web UI's /resume: the CLI/TUI can resume at startup, but
        the browser had no way to switch to a saved session mid-run. Rebuilds
        the frozen session (system prompt + tool schemas) and replays history
        — a loaded file alone leaves the model with an empty conversation.
        Returns the session's turns, or [] if the id is unknown.
        """
        turns = self.sessions.load_session(session_id)
        if turns is None:
            return []
        self._session_id = session_id
        self._rebuild_context_from_log()
        return self.sessions.current_log()

    def _rebuild_context_from_log(self) -> int:
        """Rebuild the frozen session and replay the current turn log into it.

        Shared by resume (switch to a saved session) and rewind (drop later
        turns): the live engine's prefix cache must mirror the log, or the
        model keeps seeing turns that were just rewound away.
        """
        system_prompt = self._build_system_prompt(
            self.project_memory.get_full_context())
        self.engine.freeze_session(system_prompt, self.tools.get_schemas())
        self.context_window.add_to_hot([{"role": "system", "content": system_prompt}])
        self._last_project_context = ""
        return self.sessions.replay_history(
            self.sessions.current_log(), self.engine.cache, self.context_window)

    def rewind_to(self, message_index, scope: str = "both") -> dict:
        """Per-message rewind (files / conversation / both) + rebuild context."""
        scope = (scope or "both").lower()
        files = scope in ("both", "files", "code", "workspace")
        conversation = scope in ("both", "chat", "conversation", "messages")
        res = self.sessions.rewind(message_index, files=files,
                                   conversation=conversation)
        if res.get("ok") and conversation:
            res["replayed"] = self._rebuild_context_from_log()
        return res

    async def _cli_approve(self, tool_call, context) -> bool:
        """Ask the human before a tool runs in agent mode (CLI frontend)."""
        name = getattr(tool_call, "name", "tool")
        if name in self._approved_tools:
            return True
        args = str(getattr(tool_call, "arguments", "") or "")[:160]
        prompt = (f"\n  ⚠ [agent] 允许执行 {name}？\n"
                  f"     参数: {args}\n"
                  f"     y=允许一次  a=本会话始终允许  n=拒绝 [y]: ")
        try:
            answer = (await asyncio.to_thread(input, prompt)).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if answer == "a":
            self._approved_tools.add(name)
            return True
        return answer in ("", "y", "yes")

    def save_state(self) -> None:
        """Save all persistent state before exit."""
        self.project_memory.save_all()
        # Log a session summary that is actually useful in the next session:
        # what was asked last, how many turns ran, cache hit rate. (It used to
        # record counters only — often all zeros — so "continue last task" had
        # nothing to go on.)
        if not self._session_id:
            return
        summary, turn_count = self.project_memory.build_session_summary(
            self.sessions.current_log(), self.engine.stats.hit_rate)
        self.project_memory.add_summary(
            session_id=self._session_id, summary=summary, turn_count=turn_count)

    def _build_system_prompt(self, extra_context: str = "") -> str:
        """Build the frozen system prompt for the session."""
        # Session date — frozen with the rest of the prompt (constant within
        # a session, so the prefix cache stays valid). Without it the model
        # has no time anchor at all.
        from datetime import datetime
        prompt = f"""You are ZOUWUCODE, an AI coding agent running in the terminal.

Current date: {datetime.now().strftime('%Y-%m-%d (%A)')}

You have access to a set of tools that can:
- Read, write, and edit files in the workspace
- Execute shell commands
- Run git operations
- Search the web and fetch URLs
- Use MCP (Model Context Protocol) servers

Current mode: {self._current_mode}
- plan: Read-only exploration. You can examine code but not make changes.
- agent: Interactive mode (CLI/TUI/Web). Each tool call asks for your
  approval first: y = allow once, a = always allow this tool for the
  session, n = deny. Denied calls come back to the model as errors it
  must adapt to. Headless runs (single message, dev, eval) execute
  without prompting, still constrained by the sandbox.
- yolo: Auto-approve mode. All tool calls execute automatically.

Rules:
1. Always analyze the codebase before making changes.
2. Use the most specific tool for the task.
3. After editing files, verify the changes are correct.
4. Stay within the workspace directory.
5. Never execute dangerous commands (rm -rf /, sudo, etc.).
"""
        # Append project rules (.zouwucode/rules.md)
        rules_block = self.rule_loader.get_context_block()
        if rules_block:
            prompt += f"\n\n{rules_block}"

        # Append loaded skills (.zouwucode/skills/*.md)
        skills_block = self.skills.get_context_block()
        if skills_block:
            prompt += f"\n\n=== Loaded Skills ===\n{skills_block}"

        # Repo map (Aider-style) — ranked code signatures in the frozen prompt.
        # OFF by default; when on it saves the model a lot of exploration.
        if getattr(self.config.context, "repo_map_enabled", False):
            try:
                from ..repo_map import build_repo_map, repo_map_max_tokens
                rm = build_repo_map(".", max_tokens=repo_map_max_tokens(self.config))
                if rm:
                    prompt += f"\n\n{rm}"
            except Exception as _exc:
                logger.debug("swallowed %s: %s", type(_exc).__name__, _exc)

        if extra_context:
            prompt += f"\n\n{extra_context}"
        return prompt

    # ── Message Processing ───────────────────────────────────────────────────

    async def process_message(self, message: str) -> dict:
        """Process a single user message and return the response.

        This is the core message processing pipeline used by all UIs.
        """
        # Log the turn
        self.sessions.log_turn({"role": "user", "content": message})

        # Build this round's new messages. The engine's PrefixCache already
        # holds the conversation history — resending the full history would
        # duplicate the context and balloon tokens every turn. Project
        # memory is only re-sent when its content changed.
        project_context = self.project_memory.get_full_context()
        messages = []
        if project_context and project_context != self._last_project_context:
            # NOT "system": the frozen system prompt is already first; a
            # second system message mid-conversation violates "system first"
            # on most providers. A user-role memory note is legal in-place.
            messages.append({"role": "user", "content": "[project memory]\n" + project_context})
            self._last_project_context = project_context
        messages.append({"role": "user", "content": message})

        # Add to context window (stats only)
        self.context_window.add_to_warm({"role": "user", "content": message})

        # Run the engine
        response = await self.engine.run(
            messages=messages,
            tools=self.tools.get_schemas(),
        )

        # Log the response
        self.sessions.log_turn({
            "role": "assistant",
            "content": response.content,
            "thinking": response.thinking,
            "cache_hit": response.cache_hit,
            "usage": response.usage,
        })

        # Add to context window
        self.context_window.add_to_warm({"role": "assistant", "content": response.content})

        # Auto-save project memory periodically
        if self.engine.stats.total_requests % 5 == 0:
            self.project_memory.save_all()

        return {
            "content": response.content,
            "thinking": response.thinking,
            "cache_hit": response.cache_hit,
            "usage": response.usage,
            "stats": {
                "cache_hit_rate": f"{self.engine.stats.hit_rate * 100:.1f}%",
                "total_cost": f"${self.engine.stats.total_cost:.4f}",
                "total_requests": self.engine.stats.total_requests,
                "window_stats": self.context_window.get_stats(),
            },
        }

    # ── CLI Interface ────────────────────────────────────────────────────────

    async def run_interactive(self) -> None:
        """Run the interactive CLI loop."""
        self._running = True

        # Start extensions BEFORE initialize_session() — extension tools
        # must be registered before the engine freezes tool schemas.
        await self._start_extensions()

        # Interactive CLI: install the agent-mode approval gate.
        self.coordinator.approval_callback = self._cli_approve
        # opencode-style "→ Read file …" tool trace (was TUI/Web only).
        self.engine.on_tool_event = self._on_tool_event

        session_id = self.initialize_session()

        try:
            await self._interactive_loop()
        finally:
            await self.extensions.stop()
            self.save_state()

    async def _start_extensions(self) -> None:
        try:
            await self.extensions.start(ExtensionContext(
                config=self.config,
                sandbox=self.sandbox,
                tool_registry=self.tools,
            ))
        except Exception as e:  # noqa: BLE001 — extensions must not kill startup
            print(f"  [Warning] Extension start failed: {e}")

    async def _interactive_loop(self) -> None:
        self._print_banner()
        self._print_help()

        # Show project memory status
        goal = self.project_memory.get_active_goal()
        if goal:
            print(f"  [Goal] {goal}")
        decisions = self.project_memory.get_decisions(limit=3)
        if decisions:
            print(f"  [Previous Decisions] {len(decisions)} recorded")

        while self._running:
            try:
                user_input = input(f"\n{self._prompt}").strip()
                if not user_input:
                    continue

                if user_input.startswith("/"):
                    await self._handle_command(user_input)
                else:
                    # Module message interception (e.g. "ultrawork …" prefix)
                    module_result = await self.modules.dispatch_message(user_input)
                    if module_result is not None:
                        self._render_module_result(module_result)
                        continue

                    # Stream BOTH thinking and the assistant body
                    # (opencode-style). Previously only thinking streamed and
                    # the prose appeared all at once — perceived latency is the
                    # dominant "feels slow" factor.
                    self._thinking_streaming = False
                    self._content_streaming = False
                    self.engine.on_thinking_delta = (
                        self._stream_thinking if self.config.show_thinking else None)
                    self.engine.on_content_delta = self._stream_content
                    try:
                        response = await self.process_message(user_input)
                    finally:
                        self.engine.on_thinking_delta = None
                        self.engine.on_content_delta = None
                        if self._thinking_streaming:
                            print()  # close the streamed thinking block
                        if self._content_streaming:
                            print()  # close the streamed body

                    if response["content"] and not self._content_streaming:
                        print(f"\n{response['content']}")

                    # Show cache status
                    if response["cache_hit"]:
                        print(f"\n  [⚡ Cache hit: {self.engine.stats.hit_rate * 100:.1f}%]")

                    # Show context window stats periodically
                    if self.engine.stats.total_requests > 0 and self.engine.stats.total_requests % 10 == 0:
                        ws = self.context_window.get_stats()
                        print(f"\n  [Context: {ws['hot_messages']}H + {ws['warm_messages']}W + {ws['cold_messages']}C = {ws['total_tokens']} tokens]")

            except TaskInterrupted:
                print("\n  ⏹ 任务已打断。输入「继续」从断点恢复，或输入 /clear 放弃本次任务。")
            except KeyboardInterrupt:
                if self.engine.is_running:
                    self.engine.request_interrupt(reason="CLI Ctrl+C")
                    print("\n  ⏹ 打断请求已发送，正在停止当前任务…")
                else:
                    print("\n  (Use /exit to quit)")
            except EOFError:
                break
            except Exception as e:
                # Keep the interactive loop alive when a task aborts
                # (e.g. TurnLimitExceeded from the engine's safety limits).
                print(f"\n  [Error] {e}")

    def _print_banner(self) -> None:
        """Print the application banner."""
        import shutil
        width = shutil.get_terminal_size().columns
        mode_tag = self._current_mode.upper()
        skills_tag = f"  |  Skills: {len(self.skills.list_loaded())}" if self.skills.list_loaded() else ""
        print(f"\n  {'=' * (width - 2)}")
        print(f"  ZOUWUCODE v{self.APP_VERSION}  |  Mode: {mode_tag}  |  Reasoning: {self.config.reasoning_intensity.upper()}{skills_tag}  |  Tools: {len(self.tools.get_names())}")
        print(f"  {'=' * (width - 2)}")
        print(f"  Type /help for commands, Ctrl+C to exit\n")

    @property
    def _prompt(self) -> str:
        return f"z {self._current_mode[0]} > "

    def _print_help(self) -> None:
        """Print help information."""
        print("  ── Commands ──────────────────────────────────────")
        print("  /plan       Switch to plan mode (read-only)")
        print("  /agent      Switch to agent mode (interactive)")
        print("  /yolo       Switch to yolo mode (auto-approve)")
        print("  /help       Show this help")
        print("  /exit       - Exit ZOUWUCODE")
        print("  /cache      - Show cache statistics")
        print("  /usage      - Show token usage + cost (cache savings)")
        print("  /oracle     - Second opinion from the oracle model (/oracle <q>)")
        print("  /share      - Export this session to Markdown (data_dir/shares/)")
        print("  /msg <to> <text> - Message another local session (* = broadcast)")
        print("  /inbox      - Read messages from other local sessions")
        print("  /peers      - List other local sessions on this machine")
        print("  /sessions   - List saved sessions")
        print("  /rollback   - Undo the last N turns' file changes (/rollback [N|list])")
        print("  /rewind     - Rewind to a message index (/rewind [N] [files|chat|both] | list)")
        print("  /mode       - Show current mode")
        print("  /memory     - Show project memory context")
        print("  /goal       - Show current goal")
        print("  /goal set   - Set a new goal")
        print("  /compress   - Show compression stats")
        print("  /context    - Show context window stats")
        print("  /reasoning  - Show/set reasoning intensity (low|medium|max)")
        print("  /thinking   - Toggle streaming thinking display (default: on)")
        print("  /decide     - Record a decision")
        print("  /rules      - Show/load project rules (.zouwucode/rules.md)")
        print("  /rules edit - Edit project rules in your editor")
        print("  /skill      - List/load/unload skills (.zouwucode/skills/*.md)")
        print("  /skill load <name>   - Load a skill")
        print("  /skill unload <name> - Unload a skill")
        print("  /skill list          - List available and loaded skills")
        print("  /agents     - Show sub-agent system status")
        print()
        print("  hello-my-zouwucode module (multi-agent orchestration):")
        print("  /hello-plan <task>    - Prometheus planning (or /plan <task>)")
        print("  /hello-start-work     - Atlas executes the active plan")
        print("  /hello-status         - Boulder + notepad + agent/category counts")
        print("  /hello-agents         - List the 11 built-in agents")
        print("  /hello-categories     - List task categories")
        print("  /hello-ultrawork <task>  - Full-autonomy pipeline (or prefix 'ultrawork'/'ulw')")
        print()

    # ── Module helpers ───────────────────────────────────────────────────────

    def _stream_thinking(self, delta: str) -> None:
        """CLI streaming hook — print thinking deltas as they arrive."""
        if not self._thinking_streaming:
            print("\n  ── Thinking ──")
            self._thinking_streaming = True
        print(delta, end="")
        sys.stdout.flush()

    def _stream_content(self, delta: str) -> None:
        """CLI streaming hook — print assistant text as it arrives."""
        if not self._content_streaming:
            self._content_streaming = True
        print(delta, end="")
        sys.stdout.flush()

    def _on_tool_event(self, tool_call) -> None:
        """Render an opencode-style tool invocation line (→ Read file …)."""
        name = getattr(tool_call, "name", "tool")
        args = getattr(tool_call, "arguments", "") or ""
        if isinstance(args, dict):
            import json as _json
            args = _json.dumps(args, ensure_ascii=False)
        args = str(args).replace("\n", " ")[:160]
        print(f"  → {name} {args}")

    def _on_module_event(self, etype: str, message: str, payload: dict) -> None:
        """Render a module event to the terminal."""
        tag = {"error": "[hello error]", "warning": "[hello warn]"}.get(etype, "[hello]")
        print(f"  {tag} {message}")

    def _render_module_result(self, result: dict) -> None:
        """Render a module result dict returned by the module system."""
        title = result.get("title", "Result")
        content = result.get("content", "")
        details = result.get("details") or []
        if content:
            print(f"\n  ── {title} ──\n{content}")
        for d in details:
            print(f"    {d}")

    async def _handle_command(self, cmd: str) -> None:
        """Handle a slash command."""
        parts = cmd.split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if command == "/exit":
            self.save_state()
            self.stop()
        elif command == "/help":
            self._print_help()
        elif command == "/plan":
            if arg:
                # /plan <task> → Prometheus planning via hello-my-zouwucode module
                result = await self.modules.dispatch_command("/hello-plan", arg)
                if result is not None:
                    self._render_module_result(result)
                else:
                    print("  hello-my-zouwucode not loaded (config.hello_my_zouwucode.enabled=false).")
            else:
                self.set_mode("plan")
                print("  Switched to PLAN mode (read-only)")
        elif command == "/agent":
            self.set_mode("agent")
            print("  Switched to AGENT mode (interactive approval)")
        elif command == "/yolo":
            self.set_mode("yolo")
            print("  Switched to YOLO mode (auto-approve)")
        elif command == "/mode":
            print(f"  Current mode: {self._current_mode}")
        elif command == "/usage":
            u = self.engine.get_usage_summary()
            print("  Usage & cost:")
            print(f"    model:            {u['model']}")
            print(f"    requests:         {u['requests']}")
            print(f"    prompt tokens:    {u['prompt_tokens']:,} (cached {u['cached_prompt_tokens']:,})")
            print(f"    output tokens:    {u['output_tokens']:,}")
            print(f"    cache hit rate:   {u['cache_hit_rate']}")
            print(f"    spent:            ${u['spent_usd']}")
            print(f"    cache saved:      ${u['cache_saved_usd']} ({u['saved_percent']} of full price)")
            print(f"    without cache:    ${u['would_have_cost_usd']}")
        elif command == "/oracle":
            arg = cmd[len("/oracle"):].strip()
            if not arg:
                print("  Usage: /oracle <question>  (ask the configured oracle model)")
            else:
                provider = self.router.oracle_provider(self._provider)
                if provider is None:
                    print("  Oracle not configured — set router.enabled + router.oracle_provider.")
                else:
                    print(f"  [oracle:{getattr(provider, 'model', '?')}] thinking…")
                    try:
                        resp = await provider.chat(
                            [{"role": "system", "content": ORACLE_SYSTEM},
                             {"role": "user", "content": arg}],
                            max_tokens=self.config.router.oracle_max_tokens,
                            temperature=self.config.router.oracle_temperature,
                        )
                        print(resp.content or "  (empty response)")
                    except Exception as e:
                        print(f"  Oracle error: {e}")
        elif command == "/share":
            r = self.share_current_session()
            if r.get("ok"):
                print(f"  Shared session -> {r['path']}  ({r['turns']} turns, markdown)")
            else:
                print(f"  Share failed: {r.get('error')}")
        elif command == "/msg":
            if self.bus is None:
                print("  Messaging disabled (messaging.enabled=false).")
            else:
                parts = cmd[len("/msg"):].strip().split(None, 1)
                if len(parts) < 2:
                    print("  Usage: /msg <to|*> <text>")
                else:
                    print(f"  Sent #{self.bus.send(parts[0], parts[1])} to {parts[0]}.")
        elif command == "/inbox":
            if self.bus is None:
                print("  Messaging disabled.")
            else:
                print("  Inbox:")
                print(format_inbox(self.bus.drain()))
        elif command == "/peers":
            if self.bus is None:
                print("  Messaging disabled.")
            else:
                print("  Peers (other local sessions):")
                print(format_peers(self.bus.peers()))
        elif command == "/cache":
            summary = self.engine.get_cache_summary()
            print("  Cache Performance:")
            for key, value in summary.items():
                print(f"    {key}: {value}")
        elif command == "/memory":
            ctx = self.project_memory.get_full_context()
            if ctx:
                print(f"\n{ctx}")
            else:
                print("  No project memory. Use `z --init` to initialize.")
        elif command == "/goal":
            if arg.startswith("set "):
                goal_text = arg[4:]
                self.project_memory.set_goal(goal_text)
                self.project_memory.save_all()
                print(f"  Goal set: {goal_text}")
            else:
                goal = self.project_memory.get_active_goal()
                if goal:
                    print(f"  Current goal: {goal}")
                else:
                    print("  No active goal. Use /goal set <your goal>")
        elif command == "/compress":
            print(f"  Compression level: {self.compressor_level}")
            print(f"  Max tokens: {self.max_context_tokens}")
            print(f"  Keep turns: {self.keep_turns}")
        elif command == "/reasoning":
            if arg in ("low", "medium", "max"):
                self.set_reasoning_intensity(arg)
                print(f"  Reasoning intensity set to: {arg}")
            elif arg:
                print(f"  Invalid reasoning level: {arg} (use: low, medium, max)")
            else:
                print(f"  Current reasoning intensity: {self.reasoning_intensity}")
                print("  Usage: /reasoning <low|medium|max>")
        elif command == "/thinking":
            self.config.show_thinking = not self.config.show_thinking
            state = "ON" if self.config.show_thinking else "OFF"
            print(f"  Streaming thinking display: {state}")
        elif command == "/context":
            stats = self.context_window.get_stats()
            print("  Context Window:")
            for key, value in stats.items():
                print(f"    {key}: {value}")
        elif command == "/decide":
            if arg:
                self.project_memory.add_decision("Decision", arg)
                self.project_memory.save_all()
                print(f"  Decision recorded.")
            else:
                print("  Usage: /decide <decision description>")
        elif command == "/sessions":
            sessions = self.sessions.list_sessions()
            if not sessions:
                print("  No saved sessions.")
            else:
                print("  Saved Sessions:")
                for s in sessions:
                    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(s.get("updated", 0)))
                    print(f"    {s['id']}: {s['turns']} turns ({ts})")
        elif command == "/rollback":
            if arg.strip() in ("list", "ls"):
                snaps = self.sessions.list_snapshots()
                if not snaps:
                    print("  No workspace snapshots (rollback disabled, or nothing changed yet).")
                else:
                    print("  Workspace snapshots (newest first):")
                    for s in snaps:
                        print(f"    {s['commit']}  {s['at']}  {s['label']}")
            else:
                steps = int(arg.strip()) if arg.strip().isdigit() else 1
                res = self.sessions.rollback_workspace(steps)
                if res.get("ok"):
                    print(f"  ✓ Rolled back {res['steps']} turn(s) to {res['target']}: "
                          f"{len(res.get('changed') or [])} file(s) restored, "
                          f"{len(res.get('removed') or [])} removed.")
                else:
                    print(f"  ✗ Rollback failed: {res.get('error')}")
        elif command == "/rewind":
            pa = arg.strip().split()
            if not pa or pa[0] in ("list", "ls"):
                cps = self.sessions.list_checkpoints()
                if not cps:
                    print("  No checkpoints (rollback disabled, or no turns yet).")
                else:
                    print(f"  Checkpoints (current log: {len(self.sessions.current_log())} messages):")
                    for c in cps:
                        print(f"    #{c['index']}  {c['rev'][:8]}")
            else:
                idx = int(pa[0]) if pa[0].isdigit() else 0
                scope = pa[1] if len(pa) > 1 else "both"
                res = self.rewind_to(idx, scope)
                if res.get("ok"):
                    extra = (f", {res['replayed']} messages replayed"
                             if res.get("replayed") is not None else "")
                    print(f"  ✓ Rewound to message #{res['message_index']} "
                          f"(files={'yes' if res['files'] else 'no'}, "
                          f"chat={'yes' if res['conversation'] else 'no'}{extra})")
                else:
                    print(f"  ✗ Rewind failed: {res.get('error')}")
        elif command == "/rules":
            if arg == "edit":
                # Open rules.md in editor
                path = self.rule_loader.rules_path
                if not path.exists():
                    self.rule_loader.save("# Project Rules\n\nDefine your project-specific rules here.\n")
                    print(f"  Created empty rules file: {path}")
                # Try to open in default editor
                import subprocess
                import sys
                try:
                    if sys.platform == "win32":
                        import os
                        os.startfile(str(path))  # type: ignore[attr-defined]
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", str(path)])
                    else:
                        subprocess.Popen(["xdg-open", str(path)])
                    print(f"  Opened rules file in editor: {path}")
                except Exception:
                    print(f"  Rules file: {path}")
            else:
                if self.rule_loader.exists():
                    content = self.rule_loader.load()
                    print(f"  ── Project Rules (.zouwucode/rules.md) ──")
                    print(f"  {content[:2000]}{'...' if len(content) > 2000 else ''}")
                else:
                    print("  No project rules. Create .zouwucode/rules.md or use /rules edit")
        elif command == "/skill":
            if arg.startswith("load "):
                name = arg[5:].strip()
                try:
                    self.skills.load(name)
                    print(f"  ✓ Skill '{name}' loaded. Restart session to apply.")
                except FileNotFoundError as e:
                    print(f"  {e}")
            elif arg.startswith("unload "):
                name = arg[7:].strip()
                if self.skills.unload(name):
                    print(f"  ✓ Skill '{name}' unloaded. Restart session to apply.")
                else:
                    print(f"  Skill '{name}' is not loaded.")
            elif arg == "list" or not arg:
                print("  ── Skills ──")
                for line in self.skills.get_summary().splitlines():
                    print(f"  {line}")
            else:
                print("  Usage: /skill load <name> | unload <name> | list")

        elif command == "/clear":
            # Clear the terminal (TUI/WebUI clear their chat views instead).
            os.system("cls" if os.name == "nt" else "clear")

        elif command == "/agents":
            agents = self.subagent_manager.status_summary()
            if not agents:
                print("  No sub-agents created yet. The model can delegate via the 'task' tool.")
            else:
                print("  ── Sub-agents ──")
                for a in agents:
                    err = f" — {a['error']}" if a["error"] else ""
                    print(f"  {a['id']}  {a['role']:<12} {a['status']:<10} "
                          f"{a['duration']}s  tools={a['tools']}{err}")
            exts = self.extensions.status_summary()
            if exts:
                print("  ── Extensions ──")
                for e in exts:
                    print(f"  {e['name']:<12} tools={e['tools']} dynamic={e['dynamic']}")

        else:
            # Module-provided commands (e.g. /hello-plan, /hello-start-work, …)
            result = await self.modules.dispatch_command(
                command, arg, {"session_id": self._session_id}
            )
            if result is not None:
                self._render_module_result(result)
            else:
                print(f"  Unknown command: {command}")

    def stop(self) -> None:
        """Stop the application."""
        self._running = False

    def _signal_handler(self, sig, frame) -> None:
        """Ctrl+C: interrupt a running task, otherwise exit gracefully.

        Before this, Ctrl+C always called sys.exit(0) — so it killed the
        process out from under a running task and the KeyboardInterrupt
        recovery branch in the loop was unreachable. TUI/Web already
        interrupt; the CLI now matches them.
        """
        if getattr(self.engine, "is_running", False):
            self.engine.request_interrupt(reason="CLI Ctrl+C")
            print("\n  \u23f9 打断请求已发送，正在停止当前任务…")
            return
        self.save_state()
        print("\n  Goodbye!")
        sys.exit(0)

    # ── Web UI ───────────────────────────────────────────────────────────────

    async def run_web(self, port: int = 8080) -> None:
        """Start the browser-based web UI."""
        self.initialize_session()
        print(f"\n  Starting ZOUWUCODE Web UI...")

        server = WebUIServer(
            engine=self.engine,
            tools=self.tools,
            coordinator=self.coordinator,
            config=self.config,
            sessions_manager=self.sessions,
            subagent_manager=self.subagent_manager,
            bus=self.bus,
            app=self,
            port=port,
            skills_manager=self.skills,
            rule_loader=self.rule_loader,
            modules=self.modules,
        )

        try:
            await self._start_extensions()
            await server.start()
        except KeyboardInterrupt:
            pass
        finally:
            # Always clean up — even on CancelledError/other exceptions.
            try:
                await server.stop()
            except Exception as _exc:  # noqa: BLE001 — stop() may already have run
                logger.debug("swallowed %s: %s", type(_exc).__name__, _exc)
            await self.extensions.stop()
            self.save_state()

    # ── Single Message Mode ─────────────────────────────────────────────────

    async def run_single(self, message: str) -> None:
        """Process a single message and exit."""
        self.initialize_session()

        # Module message interception (e.g. "ultrawork …" prefix)
        module_result = await self.modules.dispatch_message(message)
        if module_result is not None:
            self._render_module_result(module_result)
            self.save_state()
            return

        # Wire streaming thinking (opencode-style; /thinking toggle)
        self._thinking_streaming = False
        self._content_streaming = False
        self.engine.on_thinking_delta = (self._stream_thinking
                                         if self.config.show_thinking else None)
        self.engine.on_content_delta = self._stream_content
        try:
            response = await self.process_message(message)
        finally:
            self.engine.on_thinking_delta = None
            self.engine.on_content_delta = None
            if self._thinking_streaming:
                print()
            if self._content_streaming:
                print()

        if response["content"] and not self._content_streaming:
            print(response["content"])

        self.save_state()