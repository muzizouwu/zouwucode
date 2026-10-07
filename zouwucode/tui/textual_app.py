"""Textual-based Terminal User Interface (TUI) for ZOUWUCODE.

A full-featured terminal UI with:
- Chat view with formatted messages (user/assistant/thinking/system)
- Text input area with Enter-to-send
- Status bar showing mode, cache stats, context window info
- Mode switching (Ctrl+P/A/Y or /commands)
- Keyboard shortcuts for common actions
- Thinking indicator during processing
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Optional, ClassVar

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Input, RichLog, Static, Label, Button
from textual.reactive import reactive
from textual.binding import Binding
from textual.screen import Screen, ModalScreen
from textual import events

from ..config import ZOUWUCODEConfig
from ..logging_setup import setup_logging
from ..engine.loop import EngineLoop, TaskInterrupted
from ..runtime import create_provider, create_builtin_tools
from ..tools.registry import ToolRegistry
from ..agent.coordinator import AgentCoordinator
from ..context.memory import MemoryManager
from ..context.topics import TopicManager
from ..context.transcript import TranscriptManager
from ..context.compressor import DialogueCompressor
from ..context.sliding_window import ContextWindow
from ..sandbox.permission import PermissionManager
from ..session.manager import SessionManager
from ..project_memory import ProjectMemory
from ..skills import RuleLoader, SkillsManager
from ..tools.agent_tools import TaskTool
from ..agent.subagent import SubAgentManager
from ..extensions import ExtensionContext, ExtensionHost, LspExtension, McpExtension


# ── ANSI style helpers for RichLog messages ─────────────────────────────────
def _style(msg: str, style: str = "") -> str:
    """Wrap text in Rich markup."""
    if style:
        return f"[{style}]{msg}[/]"
    return msg


class InterruptConfirmScreen(ModalScreen):
    """Modal confirmation before interrupting the running task.

    Prevents accidental interrupts: the task only stops after the user
    explicitly confirms. Result is delivered via app-level attribute
    ``_interrupt_confirmed`` (True=confirm, False=cancel).
    """

    CSS = """
    InterruptConfirmScreen {
        align: center middle;
        background: #00000088;
    }
    #interrupt-dialog {
        width: 60;
        height: auto;
        padding: 1 2;
        border: thick $error;
        background: $surface;
    }
    #interrupt-dialog-title {
        text-style: bold;
        color: $error;
        margin-bottom: 1;
    }
    #interrupt-dialog-buttons {
        height: auto;
        align-horizontal: center;
        margin-top: 1;
    }
    #interrupt-dialog-buttons Button {
        margin: 0 2;
        min-width: 14;
    }
    """

    BINDINGS: ClassVar = [
        Binding("escape", "cancel_interrupt", "Cancel", show=False),
        Binding("enter", "confirm_interrupt", "Confirm", show=False),
    ]

    def __init__(self, task_desc: str = ""):
        super().__init__()
        self._task_desc = task_desc

    def compose(self) -> ComposeResult:
        with Vertical(id="interrupt-dialog"):
            yield Label("⚠ 确认打断当前任务？", id="interrupt-dialog-title")
            desc = self._task_desc or "AI 正在执行任务"
            yield Label(
                f"当前操作：{desc}\n"
                f"打断后可选择「继续」（AI 会接着断点继续）或「放弃」。",
                id="interrupt-dialog-desc",
            )
            with Horizontal(id="interrupt-dialog-buttons"):
                yield Button("确认打断 (Enter)", variant="error", id="btn-confirm-interrupt")
                yield Button("继续执行 (Esc)", variant="default", id="btn-cancel-interrupt")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-confirm-interrupt")

    def action_confirm_interrupt(self) -> None:
        self.dismiss(True)

    def action_cancel_interrupt(self) -> None:
        self.dismiss(False)


class ZOUWUCODETUI(App):
    """Textual-based TUI for ZOUWUCODE."""

    TITLE = "ZOUWUCODE"
    SUB_TITLE = "DeepSeek-native AI Coding Agent"

    CSS_PATH = "styles.tcss"

    BINDINGS: ClassVar = [
        Binding("ctrl+s", "cycle_mode", "Cycle Mode", show=True),
        Binding("ctrl+r", "cycle_reasoning", "Reasoning", show=True),
        Binding("ctrl+l", "clear_chat", "Clear", show=True),
        # Esc: interrupt while a task runs, focus input when idle.
        Binding("escape", "interrupt", "Interrupt", show=True),
        Binding("ctrl+c", "copy", "Copy", show=False),
    ]

    # ── Reactive properties ─────────────────────────────────────────────────
    current_mode = reactive("agent")
    status_text = reactive("Ready")
    cache_stats = reactive("")
    context_stats = reactive("")

    def __init__(self, config_path: Optional[Path] = None, skill_names: list[str] | None = None):
        super().__init__()
        self.config = ZOUWUCODEConfig.load(config_path)
        self.data_dir = Path(self.config.data_dir)

        # ── Logging (rotating file under <data_dir>/logs/) ────────────
        setup_logging(self.data_dir, self.config.log_level)

        # ── Project Memory ──────────────────────────────────────────────
        self.project_memory = ProjectMemory()
        self.project_memory.load_all()

        # ── LLM Provider ────────────────────────────────────────────────
        self._provider = self._create_provider()

        # ── Engine ───────────────────────────────────────────────────────
        self.engine = EngineLoop(self.config, self._provider)
        # opencode-style tool-call rendering ("→ Read file …")
        self.engine.on_tool_event = self._on_tool_event

        # ── Sandbox ──────────────────────────────────────────────────────
        self.sandbox = PermissionManager(self.config.sandbox)
        self.sandbox.set_workspace(Path.cwd())  # scope file tools to the workspace

        # ── Tools ────────────────────────────────────────────────────────
        self.tools = ToolRegistry()

        # ── Agent ────────────────────────────────────────────────────────
        self.coordinator = AgentCoordinator(self.config, self.engine, self.tools)
        self.engine.set_tool_executor(self.coordinator.execute_tool)

        # ── Sub-agents & extensions (MCP/LSP reserved space) ─────────────
        self._setup_agents_and_extensions()

        # ── Tools registration (built-in + extension-contributed) ────────
        self._setup_tools()

        # ── Context Management ───────────────────────────────────────────
        self.memory = MemoryManager(Path.cwd().resolve())
        self.topics = TopicManager(Path.cwd().resolve())
        self.transcripts = TranscriptManager(self.data_dir)

        # ── Sliding Window + Compression ─────────────────────────────────
        self.compressor = DialogueCompressor(level="balanced")
        self.context_window = ContextWindow()
        self.context_window.set_compressor(self.compressor)
        self.compressor_level = "balanced"
        self.max_context_tokens = 1048576
        self.keep_turns = 50

        # ── Session ──────────────────────────────────────────────────────
        self.sessions = SessionManager(self.config)
        self._session_id = None

        # ── Rules & Skills ─────────────────────────────────────────────
        workspace = Path.cwd().resolve()
        self.rule_loader = RuleLoader(workspace)
        self.skills = SkillsManager(workspace)
        self._preload_skills = skill_names or []

        # ── Module system (loadable extensions; hello-my-zouwucode) ─────
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
            except Exception:
                pass  # module optional — never block TUI startup

        # ── State ────────────────────────────────────────────────────────
        self._processing = False
        self._interrupting = False  # True while waiting for the task to stop
        self._last_project_context = ""  # only re-send project memory when it changes
        self._show_thinking = self.config.show_thinking  # /thinking toggles thinking-block display
        self._thinking_buf = ""  # line buffer for streamed thinking deltas

    def _create_provider(self):
        return create_provider(self.config)

    def _setup_tools(self):
        """Register all built-in tools.

        Extension-contributed tools (MCP/LSP) are registered by
        ExtensionHost.start() — which must run before freeze_session().
        """
        self.tools.register_all(create_builtin_tools(self.sandbox, self.config))

    def _setup_agents_and_extensions(self):
        """Wire the sub-agent system and the extension layer (MCP/LSP)."""
        # Sub-agents: isolated engine per worker, main-engine interrupt cascade
        self.subagent_manager = SubAgentManager(
            self.config, self._provider, self.coordinator,
        )
        self.subagent_manager.bind_main_engine(self.engine)
        self.tools.register(TaskTool(self.subagent_manager))

        # Extensions: MCP / LSP — reserved integration space, inactive
        # unless configured in config.yaml
        self.extensions = ExtensionHost()
        self.extensions.register(McpExtension(self.config.extensions.mcp_servers))
        self.extensions.register(LspExtension(self.config.extensions.lsp_enabled))

    # ── Compose UI ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        """Create the TUI layout."""
        # Header
        with Container(id="header"):
            yield Static("ZOUWUCODE", id="header-title")
            yield Static(f"[bold]{self.current_mode.upper()}[/]", id="header-mode")

        # Chat view
        with Container(id="chat-container"):
            yield RichLog(
                id="chat-view",
                markup=True,
                highlight=True,
                wrap=True,
                max_lines=10000,
            )

        # Input area
        with Container(id="input-area"):
            yield Input(
                placeholder="Type a message... (Ctrl+Enter for newline, /help for commands)",
                id="input-box",
            )

        # Status bar
        with Horizontal(id="status-bar"):
            yield Static("Ready", id="status-left")
            yield Static("", id="status-right")

        # Footer — opencode-style: progress blocks + shortcut hints
        with Horizontal(id="footer"):
            yield Static(
                " Ctrl+S:Cycle  Ctrl+R:Reasoning  Ctrl+L:Clear  Esc:Interrupt  /help:Commands  /thinking:Show/Hide",
                id="shortcuts",
            )
            yield Static("■■⬝⬝⬝⬝⬝⬝⬝⬝ 0%", id="footer-progress")

    # ── Lifecycle hooks ──────────────────────────────────────────────────────

    async def on_mount(self) -> None:
        """Called when the app is mounted."""
        # Start extensions BEFORE freeze_session() — extension tools must be
        # registered before the engine freezes tool schemas.
        try:
            await self.extensions.start(ExtensionContext(
                config=self.config,
                sandbox=self.sandbox,
                tool_registry=self.tools,
            ))
        except Exception:  # noqa: BLE001 — extensions must not kill startup
            pass

        # Preload skills specified via --skill CLI argument
        for name in self._preload_skills:
            try:
                self.skills.load(name)
            except FileNotFoundError as e:
                chat = self.query_one("#chat-view", RichLog)
                chat.write(_style(f"  [Warning] {e}", "yellow"))

        # Initialize session
        self._session_id = self.sessions.start_session()
        system_prompt = self._build_system_prompt()
        self.engine.freeze_session(system_prompt, self.tools.get_schemas())
        self.context_window.add_to_hot([
            {"role": "system", "content": system_prompt},
        ])

        chat = self.query_one("#chat-view", RichLog)
        chat.write("")
        chat.write(_style("  ╭──────────────────────────────────────────────────╮", "bold cyan"))
        chat.write(_style("  │  ███████╗ ██████╗ ██╗   ██╗██╗   ██╗██╗  ██╗   │", "bold cyan"))
        chat.write(_style("  │  ╚══███╔╝██╔═══██╗██║   ██║██║   ██║██║ ██╔╝   │", "bold cyan"))
        chat.write(_style("  │    ███╔╝ ██║   ██║██║   ██║██║   ██║█████╔╝    │", "bold cyan"))
        chat.write(_style("  │   ███╔╝  ██║   ██║██║   ██║██║   ██║██╔═██╗    │", "bold cyan"))
        chat.write(_style("  │  ███████╗╚██████╔╝╚██████╔╝╚██████╔╝██║  ██╗   │", "bold cyan"))
        chat.write(_style("  │  ╚══════╝ ╚═════╝  ╚═════╝  ╚═════╝ ╚═╝  ╚═╝   │", "bold cyan"))
        chat.write(_style("  │  DeepSeek-native AI Coding Agent — 1M Context   │", "dim"))
        chat.write(_style("  ╰──────────────────────────────────────────────────╯", "bold cyan"))
        chat.write("")

        # opencode-style session/workflow summary
        chat.write(_style("  ┌─ session ───────────────────────────────────────┐", "dim"))
        chat.write(_style(f"  │  {Path.cwd().resolve()}", "dim"))
        chat.write(_style(f"  │  Mode: {self.current_mode.upper()}   Reasoning: {self.config.reasoning_intensity.upper()}", "dim"))
        loaded = self.modules.loaded()
        module_line = ", ".join(loaded) if loaded else "—"
        chat.write(_style(f"  │  Modules: {module_line}", "dim"))
        chat.write(_style(f"  │  Tools: {len(self.tools.get_names())}   Skills: {len(self.skills.list_loaded())}", "dim"))
        chat.write(_style("  └──────────────────────────────────────────────────┘", "dim"))
        chat.write("")

        # Show project memory info
        goal = self.project_memory.get_active_goal()
        if goal:
            chat.write(_style(f"  Goal: {goal}", "yellow"))
        decisions = self.project_memory.get_decisions(limit=3)
        if decisions:
            chat.write(_style(f"  Decisions: {len(decisions)} recorded", "dim"))
        skills_loaded = len(self.skills.list_loaded())
        skills_tag = f"  |  Skills: {skills_loaded}" if skills_loaded else ""
        chat.write(_style(f"  Mode: {self.current_mode.upper()}  |  Reasoning: {self.config.reasoning_intensity.upper()}{skills_tag}  |  Tools: {len(self.tools.get_names())}", "green"))
        chat.write("")

        # Focus the input
        self.query_one("#input-box", Input).focus()

        # Update status bar
        self._update_status()

    def on_unmount(self) -> None:
        """Save state on exit."""
        self.project_memory.save_all()
        if self._session_id:
            self.project_memory.add_summary(
                session_id=self._session_id,
                summary=f"Session with {self.engine.stats.total_requests} turns, "
                        f"cache hit rate {self.engine.stats.hit_rate * 100:.1f}%",
                turn_count=self.engine.stats.total_requests,
            )

    # ── Input handling ───────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key in input box."""
        if self._processing:
            return

        text = event.value.strip()
        if not text:
            return

        # Clear the input
        self.query_one("#input-box", Input).value = ""

        # Handle commands
        if text.startswith("/"):
            asyncio.create_task(self._handle_command(text))
        else:
            asyncio.create_task(self._handle_message(text))

    def on_input_key(self, event: events.Key) -> None:
        """Handle special keys in input.

        Ctrl+Enter -> newline (handled by Textual's Input widget)
        Ctrl+S    -> cycle mode (plan → agent → yolo → plan)
        """
        if event.key == "ctrl+s":
            self.action_cycle_mode()
            event.prevent_default()
            event.stop()
        elif event.key == "ctrl+r":
            self.action_cycle_reasoning()
            event.prevent_default()
            event.stop()

    # ── Message handling ─────────────────────────────────────────────────────

    async def _handle_message(self, message: str) -> None:
        """Process a user message."""
        self._processing = True
        chat = self.query_one("#chat-view", RichLog)

        # Show user message (opencode-style ┃ frame)
        chat.write(_style(f"\n  ┃ You", "bold blue"))
        chat.write(_style(f"  ┃ {message}", "blue"))

        # Modules may intercept messages ("ultrawork ..." / "ulw ..." prefix)
        module_result = await self.modules.dispatch_message(message)
        if module_result is not None:
            self._render_module_result(module_result)
            self._processing = False
            self.query_one("#input-box", Input).focus()
            return

        # Update status
        self._update_status("Processing... (Esc 打断)")

        try:
            # Build this round's new messages. The engine's PrefixCache
            # already holds the conversation history — resending the full
            # history would duplicate the context and balloon tokens every
            # turn. Project memory is only re-sent when it changed.
            project_context = self.project_memory.get_full_context()
            messages = []
            if project_context and project_context != self._last_project_context:
                messages.append({"role": "system", "content": project_context})
                self._last_project_context = project_context
            messages.append({"role": "user", "content": message})

            # Add to context window (stats only)
            self.context_window.add_to_warm({"role": "user", "content": message})

            # Wire streaming thinking (opencode-style; /thinking toggle)
            self._thinking_buf = ""
            if self._show_thinking:
                self.engine.on_thinking_delta = self._on_thinking_delta
            else:
                self.engine.on_thinking_delta = None
                chat.write(_style("  💭 Thinking...", "dim italic"))

            try:
                # Run the engine
                response = await self.engine.run(
                    messages=messages,
                    tools=self.tools.get_schemas(),
                )
            finally:
                self.engine.on_thinking_delta = None
                if self._thinking_buf:
                    chat.write(_style(f"  💭 {self._thinking_buf}", "dim italic"))
                    self._thinking_buf = ""

            # Show assistant response
            if response.content:
                chat.write(_style(f"\n  ┃ assistant", "bold green"))
                chat.write(f"  ┃ {response.content}")

            # Show cache status
            if response.cache_hit:
                hr = self.engine.stats.hit_rate * 100
                chat.write(_style(f"  ⚡ Cache hit: {hr:.1f}%", "green"))

            # Log the turn
            self.sessions.log_turn({
                "role": "user", "content": message,
            })
            self.sessions.log_turn({
                "role": "assistant", "content": response.content,
                "thinking": response.thinking,
                "cache_hit": response.cache_hit,
                "usage": response.usage,
            })

            # Add to context window
            self.context_window.add_to_warm({
                "role": "assistant", "content": response.content,
            })

            # Auto-save project memory periodically
            if self.engine.stats.total_requests % 5 == 0:
                self.project_memory.save_all()

            # Update status
            self._update_status()

        except TaskInterrupted as e:
            # ── User interrupted the task ──
            self._interrupting = False
            chat.write(_style(f"\n  ⏹ 任务已打断 — {e}", "bold yellow"))
            chat.write(_style("  ┌─ 恢复选项 ─────────────────────────────┐", "dim"))
            chat.write(_style("  │  继续任务：直接输入「继续」或「continue」   │", "dim"))
            chat.write(_style("  │  放弃任务：输入 /clear 清空对话后重新开始   │", "dim"))
            chat.write(_style("  └────────────────────────────────────────┘", "dim"))
            self._update_status("Interrupted (已打断 — 可输入「继续」恢复)")
            self.sessions.log_turn({"role": "system", "content": f"[interrupted] {e}"})

        except Exception as e:
            chat.write(_style(f"  Error: {e}", "red"))
            self._update_status(f"Error: {e}")

        finally:
            self._processing = False
            self._interrupting = False
            self.query_one("#input-box", Input).focus()

    # ── Module events ────────────────────────────────────────────────────────

    def _on_module_event(self, etype: str, message: str, payload: dict) -> None:
        """Render a module event into the chat view."""
        chat = self.query_one("#chat-view", RichLog)
        style = {
            "system": "bold magenta",
            "intent": "cyan",
            "plan": "bold yellow",
            "task": "bold cyan",
            "error": "red",
            "warning": "yellow",
        }.get(etype, "magenta")
        chat.write(_style(f"  ── [hello] {message}", style))

    def _render_module_result(self, result: dict) -> None:
        """Render a module result dict into the chat view."""
        chat = self.query_one("#chat-view", RichLog)
        title = result.get("title", "Result")
        content = result.get("content", "")
        details = result.get("details") or []
        if content:
            chat.write(_style(f"\n  ── {title} ──", "bold green"))
            chat.write(f"  {content}")
        for d in details:
            chat.write(f"    {d}")

    def _on_tool_event(self, tool_call) -> None:
        """Render an opencode-style tool invocation line ("→ Read file …")."""
        try:
            chat = self.query_one("#chat-view", RichLog)
            name = getattr(tool_call, "name", "tool")
            args = getattr(tool_call, "arguments", "") or ""
            if isinstance(args, dict):
                args = json.dumps(args, ensure_ascii=False)
            summary = f"  → {name} {args}".rstrip()
            chat.write(_style(summary[:200], "magenta"))
        except Exception:
            pass  # never break the engine on UI hiccups

    def _on_thinking_delta(self, delta: str) -> None:
        """Stream thinking deltas into the chat view (opencode-style, live).

        RichLog only appends lines, so deltas are buffered and flushed as
        complete lines. A trailing partial line is flushed when the turn ends.
        """
        if not self._show_thinking:
            return
        try:
            self._thinking_buf += delta
            while "\n" in self._thinking_buf:
                line, self._thinking_buf = self._thinking_buf.split("\n", 1)
                if line.strip():
                    chat = self.query_one("#chat-view", RichLog)
                    chat.write(_style(f"  💭 {line}", "dim italic"))
        except Exception:
            pass  # never break the engine on UI hiccups

    async def _handle_command(self, cmd: str) -> None:
        """Handle a slash command."""
        chat = self.query_one("#chat-view", RichLog)
        parts = cmd.split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if command == "/help":
            chat.write(_style("  Commands:", "bold yellow"))
            chat.write("    /plan          - Switch to plan mode (read-only)")
            chat.write("    /agent         - Switch to agent mode (interactive)")
            chat.write("    /yolo          - Switch to yolo mode (auto-approve)")
            chat.write("    /help          - Show this help")
            chat.write("    /exit          - Exit ZOUWUCODE")
            chat.write("    /cache         - Show cache statistics")
            chat.write("    /sessions      - List saved sessions")
            chat.write("    /mode          - Show current mode")
            chat.write("    /memory        - Show project memory context")
            chat.write("    /goal          - Show current goal")
            chat.write("    /goal set      - Set a new goal")
            chat.write("    /compress      - Show compression stats")
            chat.write("    /context       - Show context window stats")
            chat.write("    /reasoning     - Show/set reasoning intensity")
            chat.write("    /decide        - Record a decision")
            chat.write("    /thinking      - Toggle thinking-block display")
            chat.write("    /rules         - Show project rules (.zouwucode/rules.md)")
            chat.write("    /rules edit    - Edit project rules")
            chat.write("    /skill list    - List available and loaded skills")
            chat.write("    /skill load    - Load a skill by name")
            chat.write("    /skill unload  - Unload a skill by name")
            chat.write("    /agents        - Show sub-agent system status")
            chat.write("")
            chat.write("  hello-my-zouwucode module (multi-agent orchestration):")
            chat.write("    /hello-plan <task>  - Prometheus planning (or /plan <task>)")
            chat.write("    /hello-start-work   - Atlas executes the active plan")
            chat.write("    /hello-status       - Boulder + notepad + counts")
            chat.write("    /hello-agents       - List the 11 built-in agents")
            chat.write("    /hello-categories   - List task categories")
            chat.write("    /hello-ultrawork    - Full-autonomy pipeline (or 'ultrawork'/'ulw' prefix)")
            chat.write("")
            chat.write("  Keyboard shortcuts:")
            chat.write("    Ctrl+S     - Cycle mode (plan → agent → yolo)")
            chat.write("    Ctrl+R     - Cycle reasoning (low/medium/max)")
            chat.write("    Ctrl+L     - Clear chat")
            chat.write("    Esc        - Interrupt running task (with confirm dialog); focus input when idle")

        elif command == "/exit":
            # Stop extensions synchronously — a fire-and-forget task would be
            # cancelled when the event loop closes, leaking MCP subprocesses.
            await self.extensions.stop()
            self.on_unmount()
            self.exit()

        elif command == "/agents":
            agents = self.subagent_manager.status_summary()
            if not agents:
                chat.write(_style("  ⓘ 尚未创建子 Agent。模型可通过 'task' 工具委派并行任务。", "yellow"))
            else:
                chat.write(_style("  ── Sub-agents ──", "bold cyan"))
                for a in agents:
                    err = f" — {a['error']}" if a["error"] else ""
                    chat.write(f"    {a['id']}  {a['role']:<12} {a['status']:<10} "
                               f"{a['duration']}s  tools={a['tools']}{err}")
            exts = self.extensions.status_summary()
            if exts:
                chat.write(_style("  ── Extensions ──", "bold cyan"))
                for e in exts:
                    chat.write(f"    {e['name']:<12} tools={e['tools']} dynamic={e['dynamic']}")

        elif command == "/plan":
            if arg:
                result = await self.modules.dispatch_command(
                    "/hello-plan", arg, {"session_id": self._session_id}
                )
                if result is not None:
                    self._render_module_result(result)
                else:
                    chat.write(_style(
                        "  hello-my-zouwucode module is not loaded — /plan <task> unavailable.",
                        "yellow"))
            else:
                self.action_set_mode("plan")
                chat.write(_style("  Switched to PLAN mode (read-only)", "green"))

        elif command == "/agent":
            self.action_set_mode("agent")
            chat.write(_style("  Switched to AGENT mode (interactive)", "green"))

        elif command == "/yolo":
            self.action_set_mode("yolo")
            chat.write(_style("  Switched to YOLO mode (auto-approve)", "green"))

        elif command == "/mode":
            chat.write(_style(f"  Current mode: {self.current_mode.upper()}", "cyan"))

        elif command == "/cache":
            summary = self.engine.get_cache_summary()
            chat.write(_style("  Cache Performance:", "bold cyan"))
            for key, value in summary.items():
                chat.write(f"    {key}: {value}")

        elif command == "/memory":
            ctx = self.project_memory.get_full_context()
            if ctx:
                chat.write(_style(f"\n{ctx}", "dim"))
            else:
                chat.write("  No project memory. Use `z --init` to initialize.")

        elif command == "/goal":
            if arg.startswith("set "):
                goal_text = arg[4:]
                self.project_memory.set_goal(goal_text)
                self.project_memory.save_all()
                chat.write(_style(f"  Goal set: {goal_text}", "green"))
            else:
                goal = self.project_memory.get_active_goal()
                if goal:
                    chat.write(_style(f"  Current goal: {goal}", "yellow"))
                else:
                    chat.write("  No active goal. Use /goal set <your goal>")

        elif command == "/compress":
            chat.write(_style(f"  Compression:", "bold cyan"))
            chat.write(f"    Level: {self.compressor_level}")
            chat.write(f"    Max tokens: {self.max_context_tokens}")
            chat.write(f"    Keep turns: {self.keep_turns}")

        elif command == "/reasoning":
            if arg in ("low", "medium", "max"):
                self.set_reasoning_intensity(arg)
                chat.write(_style(f"  Reasoning intensity set to: {arg}", "green"))
            elif arg:
                chat.write(_style(f"  Invalid reasoning level: {arg} (use: low, medium, max)", "red"))
            else:
                chat.write(_style(f"  Reasoning intensity: {self.config.reasoning_intensity}", "cyan"))
                chat.write("  Usage: /reasoning <low|medium|max>")

        elif command == "/context":
            stats = self.context_window.get_stats()
            chat.write(_style("  Context Window:", "bold cyan"))
            for key, value in stats.items():
                chat.write(f"    {key}: {value}")

        elif command == "/decide":
            if arg:
                self.project_memory.add_decision("Decision", arg)
                self.project_memory.save_all()
                chat.write(_style("  Decision recorded.", "green"))
            else:
                chat.write("  Usage: /decide <decision description>")

        elif command == "/sessions":
            sessions = self.sessions.list_sessions()
            if not sessions:
                chat.write("  No saved sessions.")
            else:
                chat.write(_style("  Saved Sessions:", "bold cyan"))
                for s in sessions:
                    ts = time.strftime("%Y-%m-%d %H:%M",
                                       time.localtime(s.get("updated", 0)))
                    chat.write(f"    {s['id']}: {s['turns']} turns ({ts})")

        elif command == "/clear":
            self.query_one("#chat-view", RichLog).clear()

        elif command == "/thinking":
            self._show_thinking = not self._show_thinking
            self.config.show_thinking = self._show_thinking
            state = "ON" if self._show_thinking else "OFF"
            chat.write(_style(f"  Thinking display: {state}", "green"))

        elif command == "/rules":
            if arg == "edit":
                path = self.rule_loader.rules_path
                if not path.exists():
                    self.rule_loader.save("# Project Rules\n\nDefine your project-specific rules here.\n")
                    chat.write(_style(f"  Created empty rules file: {path}", "green"))
                import subprocess
                try:
                    subprocess.Popen(["notepad", str(path)])
                    chat.write(_style(f"  Opened rules file in editor", "green"))
                except Exception:
                    chat.write(_style(f"  Rules file: {path}", "cyan"))
            else:
                if self.rule_loader.exists():
                    content = self.rule_loader.load()
                    chat.write(_style("  ── Project Rules (.zouwucode/rules.md) ──", "bold cyan"))
                    chat.write(f"  {content[:2000]}{'...' if len(content) > 2000 else ''}")
                else:
                    chat.write("  No project rules. Use /rules edit to create.")

        elif command == "/skill":
            if arg.startswith("load "):
                name = arg[5:].strip()
                try:
                    self.skills.load(name)
                    chat.write(_style(f"  ✓ Skill '{name}' loaded. Restart session to apply.", "green"))
                except FileNotFoundError as e:
                    chat.write(_style(f"  {e}", "yellow"))
            elif arg.startswith("unload "):
                name = arg[7:].strip()
                if self.skills.unload(name):
                    chat.write(_style(f"  ✓ Skill '{name}' unloaded. Restart session to apply.", "green"))
                else:
                    chat.write(_style(f"  Skill '{name}' is not loaded.", "yellow"))
            elif arg == "list" or not arg:
                chat.write(_style("  ── Skills ──", "bold cyan"))
                for line in self.skills.get_summary().splitlines():
                    chat.write(f"  {line}")
            else:
                chat.write("  Usage: /skill load <name> | unload <name> | list")

        else:
            # Generic module dispatch — modules decide whether they handle it.
            result = await self.modules.dispatch_command(
                command, arg, {"session_id": self._session_id}
            )
            if result is not None:
                self._render_module_result(result)
            else:
                chat.write(_style(f"  Unknown command: {command}", "red"))

        self.query_one("#input-box", Input).focus()

    # ── Actions ──────────────────────────────────────────────────────────────

    def action_set_mode(self, mode: str) -> None:
        """Switch modes (bound to /commands)."""
        if mode in ("plan", "agent", "yolo"):
            self.current_mode = mode
            self.engine.set_mode(mode)
            self.query_one("#header-mode", Static).update(f"[bold]{mode.upper()}[/]")
            chat = self.query_one("#chat-view", RichLog)
            chat.write(_style(f"  Switched to {mode.upper()} mode", "green"))
            self._update_status()

    def action_cycle_mode(self) -> None:
        """Cycle through modes: plan → agent → yolo → plan (bound to Ctrl+S)."""
        modes = ["plan", "agent", "yolo"]
        current = self.current_mode
        next_idx = (modes.index(current) + 1) % len(modes) if current in modes else 1
        next_mode = modes[next_idx]
        self.action_set_mode(next_mode)

    def action_clear_chat(self) -> None:
        """Clear the chat view (bound to Ctrl+L)."""
        self.query_one("#chat-view", RichLog).clear()
        self._update_status()

    def action_focus_input(self) -> None:
        """Focus the input box (bound to Escape)."""
        self.query_one("#input-box", Input).focus()

    def action_interrupt(self) -> None:
        """Interrupt the running task (bound to Esc).

        While a task is running this shows a confirmation modal first to
        prevent accidental interrupts; on confirmation, requests the engine
        to stop at the next safe point. When idle, falls back to the
        original Esc behaviour of focusing the input box.
        """
        chat = self.query_one("#chat-view", RichLog)

        # Idle → keep the original Esc behaviour: focus the input box.
        if not self._processing or not self.engine.is_running:
            if not self._interrupting:
                self.action_focus_input()
            return

        # Already interrupting — don't stack another modal.
        if self._interrupting:
            return

        # push_screen's callback fires on dismiss with the screen result:
        # True = confirmed interrupt, False/None = cancelled.
        self.push_screen(
            InterruptConfirmScreen(),
            callback=self._on_interrupt_modal_result,
        )

    def _on_interrupt_modal_result(self, confirmed) -> None:
        """Handle the confirmation modal result."""
        chat = self.query_one("#chat-view", RichLog)
        if confirmed:
            self._do_request_interrupt()
        elif self._processing:
            chat.write(_style("  ⓘ 已取消打断，任务继续执行。", "dim"))

    def _do_request_interrupt(self) -> None:
        """Send the interrupt request to the engine and update the UI."""
        chat = self.query_one("#chat-view", RichLog)
        try:
            ok = self.engine.request_interrupt(reason="TUI Esc")
        except Exception as e:  # never let interrupt failures kill the TUI
            chat.write(_style(f"  ✗ 打断请求失败: {e}", "red"))
            return
        if ok:
            self._interrupting = True
            self._update_status("Interrupting... (等待任务停止)")
            chat.write(_style("  ⏹ 已发送打断请求，正在等待任务安全停止…", "bold yellow"))
        else:
            chat.write(_style("  ⓘ 任务已结束，无需打断。", "yellow"))

    def action_copy(self) -> None:
        """Copy selected text."""
        # Handled by terminal
        pass

    def action_cycle_reasoning(self) -> None:
        """Cycle reasoning intensity (bound to Ctrl+R)."""
        levels = ["low", "medium", "max"]
        current = self.config.reasoning_intensity
        next_idx = (levels.index(current) + 1) % len(levels) if current in levels else 1
        next_level = levels[next_idx]
        self.set_reasoning_intensity(next_level)
        chat = self.query_one("#chat-view", RichLog)
        chat.write(_style(f"  Reasoning: {next_level.upper()}", "cyan"))

    def set_reasoning_intensity(self, level: str) -> None:
        """Set reasoning effort level (low / medium / max)."""
        if level in ("low", "medium", "max"):
            self.config.reasoning_intensity = level
            self.engine.set_reasoning_intensity(level)
            self._update_status()

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _update_status(self, message: Optional[str] = None) -> None:
        """Update the status bar."""
        left = self.query_one("#status-left", Static)
        right = self.query_one("#status-right", Static)

        if message:
            left.update(message)
        else:
            goal = self.project_memory.get_active_goal()
            mode_text = f"[bold green]{self.current_mode.upper()}[/]"
            reasoning_text = f"[bold cyan]R:{self.config.reasoning_intensity.upper()}[/]"
            if goal:
                left.update(f"Mode: {mode_text} | {reasoning_text} | Goal: {goal[:50]}")
            else:
                left.update(f"Mode: {mode_text} | {reasoning_text}")

        # Right side: reasoning + cache + context stats
        reasoning_text = f"Reasoning: {self.config.reasoning_intensity.upper()}"
        cache_text = f"Cache: {self.engine.stats.hit_rate * 100:.0f}%"
        ws = self.context_window.get_stats()
        context_text = f"Context: {ws['total_tokens']}t"
        cost_text = f"${self.engine.stats.total_cost:.4f}"
        right.update(f"{reasoning_text} | {cache_text} | {context_text} | {cost_text}")

        # Footer progress bar (opencode-style ■■⬝⬝ blocks + pct)
        try:
            used = int(ws.get("total_tokens", 0) or 0)
            pct = min(1.0, used / self.max_context_tokens) if self.max_context_tokens else 0.0
            n = min(10, int(round(pct * 10)))
            bar = "■" * n + "⬝" * (10 - n)
            self.query_one("#footer-progress", Static).update(
                f"{bar} {pct * 100:.0f}%"
            )
        except Exception:
            pass

    def _build_system_prompt(self) -> str:
        """Build the frozen system prompt."""
        prompt = f"""You are ZOUWUCODE, an AI coding agent running in the terminal.

Current mode: {self.current_mode}
- plan: Read-only exploration. No changes.
- agent: Interactive mode. Tool calls require approval.
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

        return prompt


def run_tui(config_path: Optional[Path] = None, skill_names: list[str] | None = None) -> None:
    """Run the Textual TUI application."""
    app = ZOUWUCODETUI(config_path, skill_names=skill_names)
    app.run()