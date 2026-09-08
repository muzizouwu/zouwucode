"""Entry point for ZOUWUCODE — CLI, TUI, and Web UI launcher."""

import asyncio
import sys
from pathlib import Path

from .tui.app import ZOUWUCODEApp


def main():
    """Main entry point for the CLI application."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="zouwucode",
        description="ZOUWUCODE - DeepSeek-native AI coding agent for your terminal",
        epilog="Examples:\n"
               "  zouwucode                    # Start CLI (interactive mode)\n"
               "  zouwucode --tui              # Start Textual TUI (full-featured)\n"
               "  zouwucode --web              # Start browser UI\n"
               "  zouwucode --mode plan        # Start in plan mode\n"
               "  z                            # Quick launch (after install)\n"
               "  z --tui                      # Quick launch TUI\n"
               "  z --init                     # Init project memory\n"
               "  z --compress                 # Set compression level\n"
               "  z --help                     # Show this help",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── Core options ────────────────────────────────────────────────────────
    parser.add_argument("--config", "-c", type=str, help="Path to config file")
    parser.add_argument("--version", "-v", action="store_true", help="Show version and exit")
    parser.add_argument("--mode", "-m", choices=["plan", "agent", "yolo"], default="agent",
                        help="Default operation mode (default: agent)")
    parser.add_argument("--model", type=str, help="Model to use (e.g., deepseek-v4-flash)")
    parser.add_argument("--provider", type=str, default="deepseek", help="LLM provider (deepseek, openai)")
    parser.add_argument("--api-key", type=str, help="API key for the LLM provider")

    # ── Interface selection ─────────────────────────────────────────────────
    parser.add_argument("--tui", action="store_true", help="Start Textual terminal UI (full-featured TUI)")
    parser.add_argument("--web", action="store_true", help="Start browser-based UI")
    parser.add_argument("--port", type=int, default=8080, help="Web UI port (default: 8080)")

    # ── Project Memory ──────────────────────────────────────────────────────
    parser.add_argument("--init", action="store_true",
                        help="Initialize project memory (.zouwucode/) in current directory")
    parser.add_argument("--forget", action="store_true",
                        help="Clear all project memory for current project")
    parser.add_argument("--goal", type=str, help="Set a persistent goal for the project")

    # ── Context / Compression ───────────────────────────────────────────────
    parser.add_argument("--compress", choices=["lossless", "balanced", "aggressive", "off"],
                        default="balanced", help="Dialogue compression level (default: balanced)")
    parser.add_argument("--max-tokens", type=int, default=1048576,
                        help="Max context tokens (default: 1048576 for 1M support)")
    parser.add_argument("--keep-turns", type=int, default=50,
                        help="Number of recent turns to keep uncompressed (default: 50)")

    # ── Reasoning / Thinking ────────────────────────────────────────────────
    parser.add_argument("--reasoning", choices=["low", "medium", "max"],
                        default="medium",
                        help="Reasoning intensity: low (fast, deterministic), "
                             "medium (balanced thinking), max (deep reasoning)")

    # ── Skills / Rules ──────────────────────────────────────────────────────
    parser.add_argument("--skill", action="append", dest="skills", default=[],
                        help="Pre-load a skill on startup (can be used multiple times). "
                             "Skills are loaded from .zouwucode/skills/<name>.md")

    # ── Config management ───────────────────────────────────────────────────
    parser.add_argument("--init-config", action="store_true",
                        help="Initialize default config file and exit")
    parser.add_argument("--show-config", action="store_true",
                        help="Show current configuration and exit")

    # ── Quick command (no arguments) ────────────────────────────────────────
    parser.add_argument("message", nargs="?", help="Single message to process (non-interactive)")

    args = parser.parse_args()

    # ── Handle --version ────────────────────────────────────────────────────
    if args.version:
        from . import __version__
        print(f"ZOUWUCODE v{__version__}")
        sys.exit(0)

    # ── Handle --init-config ────────────────────────────────────────────────
    config_path = Path(args.config) if args.config else None
    if args.init_config:
        from .config import ZOUWUCODEConfig
        cfg = ZOUWUCODEConfig()
        cfg.data_dir = str(Path.cwd())
        if config_path:
            cfg.save(config_path)
        else:
            path = Path.cwd() / "config.yaml"
            cfg.save(path)
        created = config_path if config_path else Path.cwd() / "config.yaml"
        print(f"  Default config created at: {created}")
        print(f"  Edit the file to add your API keys.")
        sys.exit(0)

    # ── Handle --init (project memory) ──────────────────────────────────────
    if args.init:
        from .project_memory import ProjectMemory
        pm = ProjectMemory()
        pm.ensure_dir()
        pm.set_state("initialized", True)
        pm.set_state("description", "")
        pm.set_state("created_at", __import__("time").time())
        pm.save_all()
        print(f"  ✓ Project memory initialized: {pm._memory_dir}")
        print(f"  This directory now tracks project state across sessions.")
        if args.goal:
            pm.set_goal(args.goal)
            print(f"  ✓ Goal set: {args.goal}")
        sys.exit(0)

    # ── Handle --forget ─────────────────────────────────────────────────────
    if args.forget:
        from .project_memory import ProjectMemory
        pm = ProjectMemory()
        import shutil
        if pm._memory_dir.exists():
            shutil.rmtree(pm._memory_dir)
            print(f"  ✓ Project memory cleared: {pm._memory_dir}")
        else:
            print(f"  No project memory found in this directory.")
        sys.exit(0)

    # ── Handle --show-config ────────────────────────────────────────────────
    if args.show_config:
        from .config import ZOUWUCODEConfig
        import yaml
        cfg = ZOUWUCODEConfig.load(config_path)
        print(yaml.dump(cfg.model_dump(), default_flow_style=False))
        sys.exit(0)

    # ── Create app ──────────────────────────────────────────────────────────
    app = ZOUWUCODEApp(config_path, skill_names=args.skills)

    # Apply CLI overrides
    if args.mode:
        app.set_mode(args.mode)
    if args.model:
        if app.config.default_provider in app.config.providers:
            app.config.providers[app.config.default_provider].model = args.model
    if args.api_key:
        if app.config.default_provider in app.config.providers:
            app.config.providers[app.config.default_provider].api_key = args.api_key

    # Set compression level
    if args.compress and args.compress != "off":
        app.compressor_level = args.compress
    app.max_context_tokens = args.max_tokens
    app.keep_turns = args.keep_turns

    # Set reasoning intensity
    if args.reasoning:
        app.set_reasoning_intensity(args.reasoning)

    # Set goal if provided
    if args.goal:
        app.project_memory.set_goal(args.goal)
        app.project_memory.save_all()

    # ── Launch ──────────────────────────────────────────────────────────────
    if args.tui:
        # ── Textual TUI mode ────────────────────────────────────────────────
        from .tui.textual_app import run_tui
        run_tui(config_path, skill_names=args.skills)
    elif args.web:
        # ── Web UI mode ─────────────────────────────────────────────────────
        asyncio.run(app.run_web(port=args.port))
    elif args.message:
        # ── Single message mode (non-interactive) ───────────────────────────
        asyncio.run(app.run_single(args.message))
    else:
        # ── Interactive CLI mode ────────────────────────────────────────────
        try:
            asyncio.run(app.run_interactive())
        except KeyboardInterrupt:
            print("\n  Goodbye!")
            app.save_state()
            sys.exit(0)


if __name__ == "__main__":
    main()