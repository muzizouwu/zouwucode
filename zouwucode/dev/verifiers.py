"""Multi-layer verification pipeline for dev mode.

Pain point addressed: a single `pytest` run only checks "the tests I wrote
still pass" — it misses style regressions, type errors, security smells and
untested boundary code paths. Mature agents gate on several independent
layers instead.

Layers (each auto-detected; a layer whose tool is not installed/configured
is SKIPPED, never faked):

    1. lint        ruff check / eslint
    2. typecheck   mypy / tsc --noEmit
    3. test        pytest (optional coverage floor) / npm test
    4. security    bandit -c pyproject.toml -r <src>  (opt-in via config marker)

Every layer returns a LayerResult; the pipeline aggregates them into one
verification report. Failures are fed back to the implementing agent as
targeted, layer-labelled feedback — the agent sees exactly which gate it
broke, which keeps constraints *informative* rather than mysterious
(informational feedback preserves model performance better than blanket
prompt restrictions).
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("zouwucode.dev.verifiers")

# Output is truncated per layer so feedback stays focused and cheap.
_MAX_OUTPUT_CHARS = 4000


@dataclass
class LayerResult:
    """Outcome of one verification layer."""

    name: str                     # lint | typecheck | test | security
    command: str = ""
    passed: bool = True
    skipped: bool = False
    output: str = ""

    def render(self) -> str:
        if self.skipped:
            return f"[{self.name}] SKIPPED ({self.output or 'tool not available'})"
        mark = "PASS" if self.passed else "FAIL"
        header = f"[{self.name}] {mark}  $ {self.command}"
        if self.passed:
            return header
        return f"{header}\n{self.output[-_MAX_OUTPUT_CHARS:]}"


@dataclass
class VerificationReport:
    """Aggregated multi-layer result."""

    results: list[LayerResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed or r.skipped for r in self.results)

    def render_feedback(self) -> str:
        """Layer-labelled report for feeding back into the agent session."""
        lines = [r.render() for r in self.results]
        return "\n\n".join(lines)


async def _run_shell(cmd: str, cwd: Path, timeout: float = 600.0) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_shell(
        cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return False, f"command timed out after {timeout:.0f}s: {cmd}"
    output = stdout.decode("utf-8", errors="replace")
    return proc.returncode == 0, output[-_MAX_OUTPUT_CHARS:]


def _tool_available(name: str) -> bool:
    """True if the tool exists on PATH or as an importable Python module."""
    import shutil
    if shutil.which(name):
        return True
    return _python_module_exists(name)


def _python_module_exists(name: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# ── Layer detectors ─────────────────────────────────────────────────────────

def detect_lint_command(cwd: Path, configured: str = "") -> Optional[str]:
    if configured:
        return configured
    if (cwd / "pyproject.toml").exists() or (cwd / "ruff.toml").exists():
        if _tool_available("ruff"):
            return "ruff check ."
    if (cwd / "package.json").exists() and _tool_available("eslint"):
        return "npx eslint ."
    return None


def detect_typecheck_command(cwd: Path, configured: str = "") -> Optional[str]:
    if configured:
        return configured
    if (cwd / "mypy.ini").exists() or (cwd / ".mypy.ini").exists():
        if _tool_available("mypy"):
            return "mypy ."
    if (cwd / "pyproject.toml").exists():
        try:
            text = (cwd / "pyproject.toml").read_text(encoding="utf-8")
        except OSError:
            text = ""
        if "[tool.mypy]" in text and _tool_available("mypy"):
            return "mypy ."
    if (cwd / "tsconfig.json").exists() and _tool_available("tsc"):
        return "npx tsc --noEmit"
    return None


def detect_security_command(cwd: Path, configured: str = "") -> Optional[str]:
    """bandit is noisy on repos that never opted in; require a [tool.bandit]
    marker in pyproject.toml (or an explicit configured command)."""
    if configured:
        return configured
    pyproject = cwd / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
        except OSError:
            return None
        if "[tool.bandit]" in text and _tool_available("bandit"):
            src_dirs = [d.name for d in cwd.iterdir()
                        if d.is_dir() and (d / "__init__.py").exists()]
            targets = " ".join(src_dirs) if src_dirs else "."
            return f"bandit -c pyproject.toml -r {targets}"
    return None


def detect_test_command(cwd: Path, configured: str = "") -> Optional[str]:
    if configured:
        return configured
    if (cwd / "pytest.ini").exists() or (cwd / "tests").is_dir() \
            or (cwd / "pyproject.toml").exists():
        return "python -m pytest -q"
    if (cwd / "package.json").exists():
        try:
            pkg = json.loads((cwd / "package.json").read_text(encoding="utf-8"))
            if "test" in (pkg.get("scripts") or {}):
                return "npm test"
        except Exception:
            pass
    return None


# ── Pipeline ────────────────────────────────────────────────────────────────

async def run_verification(
    cwd: Path,
    *,
    lint_command: str = "",
    typecheck_command: str = "",
    test_command: str = "",
    security_command: str = "",
    coverage_min: float = 0.0,
) -> VerificationReport:
    """Run every available layer; skipped layers never block the gate.

    Order: cheap static gates first (lint/typecheck), then tests, then
    security — the agent gets the fastest, most actionable feedback first.
    """
    report = VerificationReport()
    cwd = Path(cwd)

    layers: list[tuple[str, Optional[str]]] = [
        ("lint", detect_lint_command(cwd, lint_command)),
        ("typecheck", detect_typecheck_command(cwd, typecheck_command)),
    ]

    test_cmd = detect_test_command(cwd, test_command)
    if test_cmd and coverage_min > 0 and "pytest" in test_cmd \
            and "--cov" not in test_cmd:
        test_cmd = f"{test_cmd} --cov --cov-fail-under={coverage_min:g}"
    layers.append(("test", test_cmd))

    layers.append(("security", detect_security_command(cwd, security_command)))

    for name, cmd in layers:
        if not cmd:
            report.results.append(LayerResult(
                name=name, skipped=True, output="tool not configured/detected"))
            continue
        passed, output = await _run_shell(cmd, cwd)
        report.results.append(LayerResult(
            name=name, command=cmd, passed=passed, output=output))
        logger.info("Verification layer %s: %s", name, "PASS" if passed else "FAIL")

    return report
