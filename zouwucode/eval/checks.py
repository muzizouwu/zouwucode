"""Deterministic acceptance checks for eval tasks — no LLM in the loop.

The LLM does the work; these functions grade it. Every check is one of:
  - file_exists / file_contains / file_absent  (workspace file assertions)
  - python_eval                                (expression must be truthy)
  - command_pass                               (shell command exit code 0)
"""

import asyncio
import sys
from pathlib import Path

_MAX_OUTPUT = 4000


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


async def run_check(check: dict, workspace: Path) -> tuple[bool, str]:
    kind = str(check.get("type", ""))
    if kind == "file_exists":
        p = workspace / str(check.get("path", ""))
        return p.exists(), f"exists={p.exists()}"
    if kind == "file_absent":
        p = workspace / str(check.get("path", ""))
        return not p.exists(), f"absent={not p.exists()}"
    if kind == "file_contains":
        p = workspace / str(check.get("path", ""))
        text = str(check.get("text", ""))
        content = _read(p)
        return text in content, f"'{text[:60]}' in {p.name}: {text in content}"
    if kind == "python_eval":
        expr = str(check.get("expr", ""))
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c",
            f"import sys; sys.path.insert(0, {str(workspace)!r}); "
            f"assert ({expr}), 'expression falsy'",
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            return False, "python_eval timed out"
        ok = proc.returncode == 0
        detail = out.decode("utf-8", "replace")[-_MAX_OUTPUT:]
        return ok, detail or ("truthy" if ok else "falsy/error")
    if kind == "command_pass":
        cmd = str(check.get("command", ""))
        proc = await asyncio.create_subprocess_shell(
            cmd, cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            return False, f"command timed out: {cmd}"
        ok = proc.returncode == 0
        return ok, ("" if ok else
                    out.decode("utf-8", "replace")[-_MAX_OUTPUT:])
    return False, f"unknown check type: {kind!r}"


async def run_checks(checks: list[dict], workspace: Path) -> list[dict]:
    """Run all checks; return per-check verdicts. All-pass = task pass."""
    results = []
    for c in checks:
        ok, detail = await run_check(c, workspace)
        results.append({"check": c.get("type", "?"),
                        "target": c.get("path") or c.get("expr")
                        or c.get("command") or "",
                        "passed": ok, "detail": detail[:500]})
    return results
