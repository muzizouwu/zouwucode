"""Main ZOUWUCODE application — wires all components together.

Integrates:
- Persistent project memory (cross-session state)
- Sliding window context manager (ultra-long context)
- Auto-dialogue compression (intelligent compression)
- CLI, TUI, and Web UI interfaces
- Quick launch support
"""

import os
import sys
import time
import signal
from pathlib import Path
from typing import Optional

from ..config import ZOUWUCODEConfig
from ..logging_setup import setup_logging
from ..engine.loop import EngineLoop, TaskInterrupted
from ..runtime import create_provider, create_builtin_tools
from ..tools.registry import ToolRegistry
from ..tools.agent_tools import TaskTool
from ..agent.coordinator import AgentCoordinator
from ..agent.subagent import SubAgentManager
from ..extensions import ExtensionContext, ExtensionHost, LspExtension, McpExtension
from ..context.memory import MemoryManager
from ..context.topics import TopicManager
from ..context.transcript import TranscriptManager
from ..context.compressor import DialogueCompressor
from ..context.sliding_window import ContextWindow
from ..sandbox.permission import PermissionManager
from ..session.manager import SessionManager
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
    APP_VERSION = "1.0.0"

    def __init__(self, config_path: Optional[Path] = None, skill_names: list[str] | None = None):
        self.config = ZOUWUCODEConfig.load(config_path)
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
            except Exception as e:  # module optional — never block startup
                print(f"  [Warning] hello-my-zouwucode module failed to load: {e}")

        # ── State ──────────────────────────────────────────────────────────
        self._current_mode = self.config.sandbox.default_mode
        self._running = False
        self._session_id: Optional[str] = None
        self._thinking_streaming = False  # set while streaming thinking deltas

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
        self.tools.register_all(create_builtin_tools(self.sandbox))

    def _setup_agents_and_extensions(self):
        """Wire the sub-agent system and the extension layer (MCP/LSP)."""
        # Sub-agents: isolated engine per worker, main-engine interrupt cascade
        self.subagent_manager = SubAgentManager(
            self.config, self._provider, self.coordinator,
        )
        self.subagent_manager.bind_main_engine(self.engine)
        self.tools.register(TaskTool(self.subagent_manager))

        # Extensions: MCP / LSP — reserved integration space, inactive
        # unless configured in config.yaml (extensions.mcp_servers / lsp_enabled)
        self.extensions = ExtensionHost()
        self.extensions.register(McpExtension(self.config.extensions.mcp_servers))
        self.extensions.register(LspExtension(self.config.extensions.lsp_enabled))

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

    def initialize_session(self, session_id: Optional[str] = None) -> str:
        """Initialize a new session with frozen cache prefix and project memory."""
        self._session_id = self.sessions.start_session(session_id)

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

        self.transcripts.start_session(self._session_id)
        return self._session_id

    def save_state(self) -> None:
        """Save all persistent state before exit."""
        self.project_memory.save_all()
        # Log session summary
        if self._session_id:
            self.project_memory.add_summary(
                session_id=self._session_id,
                summary=f"Session with {self.engine.stats.total_requests} turns, "
                        f"cache hit rate {self.engine.stats.hit_rate * 100:.1f}%",
                turn_count=self.engine.stats.total_requests,
            )

    def _build_system_prompt(self, extra_context: str = "") -> str:
        """Build the frozen system prompt for the session."""
        prompt = f"""You are ZOUWUCODE, an AI coding agent running in the terminal.

You have access to a set of tools that can:
- Read, write, and edit files in the workspace
- Execute shell commands
- Run git operations
- Search the web and fetch URLs
- Use MCP (Model Context Protocol) servers

Current mode: {self._current_mode}
- plan: Read-only exploration. You can examine code but not make changes.
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
            messages.append({"role": "system", "content": project_context})
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

                    # Wire streaming thinking (opencode-style; /thinking toggle)
                    self._thinking_streaming = False
                    if self.config.show_thinking:
                        self.engine.on_thinking_delta = self._stream_thinking
                    else:
                        self.engine.on_thinking_delta = None
                    try:
                        response = await self.process_message(user_input)
                    finally:
                        self.engine.on_thinking_delta = None
                        if self._thinking_streaming:
                            print()  # close the streamed thinking block

                    if response["content"]:
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
        print("  /sessions   - List saved sessions")
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
        elif command == "/rules":
            if arg == "edit":
                # Open rules.md in editor
                path = self.rule_loader.rules_path
                if not path.exists():
                    self.rule_loader.save("# Project Rules\n\nDefine your project-specific rules here.\n")
                    print(f"  Created empty rules file: {path}")
                # Try to open in default editor
                import subprocess
                try:
                    subprocess.Popen(["notepad", str(path)])
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
        """Handle Ctrl+C gracefully."""
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
            except Exception:  # noqa: BLE001 — stop() may already have run
                pass
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
        if self.config.show_thinking:
            self.engine.on_thinking_delta = self._stream_thinking
        try:
            response = await self.process_message(message)
        finally:
            self.engine.on_thinking_delta = None
            if self._thinking_streaming:
                print()

        if response["content"]:
            print(response["content"])

        self.save_state()