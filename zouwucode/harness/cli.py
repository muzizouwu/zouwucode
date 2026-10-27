"""``zouwucode harness`` — run a task on DeepSeek's official harness (dsh).

Kept as its own subcommand on purpose: harnessing is a different runtime from
zouwucode's own engine (dsh owns the agent loop, tools and session store), so
it is opt-in and never changes default behaviour.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from ..config import ZOUWUCODEConfig
from ..errors import describe_exc
from .deepseek import (
    INSTALL_HINT,
    HarnessError,
    HarnessRunner,
    HarnessUnavailable,
    resolve_settings,
    sdk_installed,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zouwucode harness",
        description=(
            "把任务交给 DeepSeek 官方 Harness（dsh）执行。"
            "需要先安装可选依赖 deepseek-harness-sdk。"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  zouwucode harness \"把 tests/ 里的失败用例修好\"\n"
               "  zouwucode harness --doctor\n"
               "  zouwucode harness --session demo-1 --reasoning max \"重构 config.py\"\n"
               "  echo \"跑一遍测试\" | zouwucode harness\n"
               "  zouwucode harness --json --model deepseek-v4-flash \"解释仓库结构\"",
    )
    parser.add_argument("prompt", nargs="?",
                        help="要执行的任务；省略时从 stdin 读取")
    parser.add_argument("--config", "-c", type=str, help="配置文件路径")
    parser.add_argument("--cwd", type=str, help="Agent 工作区（默认当前目录）")
    parser.add_argument("--dsh-home", type=str,
                        help="DSH_HOME（默认 <data_dir>/dsh_home）")
    parser.add_argument("--profile", type=str,
                        help="dsh profile（默认 sdk；可用 sdk-minimal）")
    parser.add_argument("--provider", type=str,
                        help="dsh 组合中已注册的 provider 路由（默认由 api_type 映射）")
    parser.add_argument("--model", type=str, help="模型 id（dsh 侧解析）")
    parser.add_argument("--reasoning", choices=["low", "medium", "max"],
                        default=None, help="推理强度（默认取 zouwucode 配置）")
    parser.add_argument("--max-tokens", type=int,
                        help="单次请求输出上限（默认取配置，49152）")
    parser.add_argument("--timeout", type=float,
                        help="单轮请求超时秒数（0/省略 = 不设限）")
    parser.add_argument("--patch", action="append", dest="patches", default=None,
                        help="追加的 cordis patch 文件（可多次指定）")
    parser.add_argument("--session", type=str,
                        help="会话 id；复用同一 id 可继续上次对话")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="以 JSON 输出结果")
    parser.add_argument("--verbose", action="store_true",
                        help="实时打印 harness 通知（子 Agent / 事件）")
    parser.add_argument("--doctor", action="store_true",
                        help="只做前置检查，不执行任务")
    return parser


def _print_err(message: str) -> None:
    print(message, file=sys.stderr)


def harness_main(argv: Optional[list[str]] = None) -> int:
    """Entry point for ``zouwucode harness``; returns a process exit code."""
    args = _build_parser().parse_args(argv)

    config_path = Path(args.config) if args.config else None
    config = ZOUWUCODEConfig.load(config_path)

    try:
        settings = resolve_settings(
            config,
            cwd=Path(args.cwd) if args.cwd else None,
            dsh_home=Path(args.dsh_home) if args.dsh_home else None,
            profile=args.profile,
            provider=args.provider,
            model=args.model,
            reasoning_effort=args.reasoning,
            max_tokens=args.max_tokens,
            request_timeout_seconds=args.timeout,
            patches=tuple(args.patches) if args.patches else None,
        )
    except ValueError as exc:  # unsupported provider mapping etc.
        _print_err(f"[harness] {exc}")
        return 2

    installed = sdk_installed()

    if args.doctor:
        print("DeepSeek Harness preflight")
        print(f"  sdk        : {'installed' if installed else 'MISSING'}")
        print(f"  dsh_home   : {settings.dsh_home} "
              f"(exists={settings.dsh_home.exists()})")
        print(f"  workspace  : {settings.cwd}")
        print(f"  profile    : {settings.profile}")
        print(f"  provider   : {settings.provider}")
        print(f"  model      : {settings.model}")
        print(f"  reasoning  : {settings.reasoning_effort or '(model default)'}")
        print(f"  max_tokens : {settings.max_tokens}")
        print(f"  timeout    : "
              f"{(str(settings.request_timeout_seconds) + 's') if settings.request_timeout_seconds else 'unbounded'}")
        if settings.patches:
            print(f"  patches    : {len(settings.patches)} file(s)")
        if not installed:
            print(f"\n安装缺失依赖后可执行：\n  {INSTALL_HINT}")
            return 2
        return 0

    prompt = args.prompt
    if not prompt:
        try:
            if not sys.stdin.isatty():
                prompt = sys.stdin.read()
        except (OSError, ValueError):
            prompt = ""
    if not (prompt or "").strip():
        _print_err("[harness] 缺少任务内容：请传入 prompt 参数，或用管道从 stdin 输入。")
        return 2

    runner = HarnessRunner(settings)
    notify = None
    if args.verbose:
        def notify(event) -> None:  # noqa: ANN001 — SDK opaque event object
            text = str(event)
            print(f"[harness] {text[:160]}", file=sys.stderr)

    try:
        result = runner.run(prompt, session_id=args.session,
                            on_notification=notify)
    except HarnessUnavailable as exc:
        _print_err(f"[harness] {exc}")
        return 2
    except HarnessError as exc:
        _print_err(f"[harness] {exc}")
        return 1
    except ValueError as exc:
        _print_err(f"[harness] {describe_exc(exc)}")
        return 2
    finally:
        runner.close()

    if args.as_json:
        print(json.dumps({
            "session_id": result.session_id,
            "finish_reason": result.finish_reason,
            "events": result.event_count,
            "notifications": result.notification_count,
            "text": result.text,
        }, ensure_ascii=False, indent=2))
    else:
        if result.text:
            print(result.text)
        if args.verbose:
            print(f"[harness] finish={result.finish_reason} "
                  f"events={result.event_count} "
                  f"notifications={result.notification_count}",
                  file=sys.stderr)
    return 0
