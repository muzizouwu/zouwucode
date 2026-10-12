"""Regression for v1.5.0: observability hardening.

The project's own audit called "silent failure" (a failing path that reports
success / swallows the error) the most dangerous bug class. This locks the
rule in: a broad `except Exception:` must never end in a bare `pass` — it must
log. These tests are static (guard against regression) plus one behavioural
check that a swallowed error actually reaches the log.

v1.7.2 strengthened the static check after an audit found leaks:
- `pass  # comment` slipped through (the old check compared the stripped line
  to exactly "pass"), so commented silent swallows are now caught too;
- `continue` is treated the same as `pass` (both discard the error silently);
- triple-quoted string bodies are excluded — the python_exec child-process
  REPL driver has no logger and its stdout IS the wire protocol, so skipping
  a malformed frame there is the only safe action.
"""

import logging
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "zouwucode"

_EXC = re.compile(r"^(\s*)except\s+Exception(\s+as\s+\w+)?\s*:(\s*#.*)?$")
# pass / continue, optionally followed by a comment.
_SILENT = re.compile(r"(pass|continue)\s*(#.*)?$")
_BARE = re.compile(r"^\s*except\s*:\s*(#.*)?$")


def _iter_code_lines(path: Path):
    """Yield (lineno, line) for lines OUTSIDE triple-quoted strings.

    Embedded string bodies (e.g. the python_exec child-process REPL driver)
    execute in a different process under different rules — no logger, stdout
    is a protocol — so they must not be judged by these static checks.
    """
    delim = None
    for i, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
        if delim is None:
            opened = None
            for d in ('"""', "'''"):
                if d in line:
                    opened = d
                    break
            if opened is None:
                yield i, line
                continue
            if opened in line.split(opened, 1)[1]:
                yield i, line          # opens and closes on this line
            else:
                delim = opened
        elif delim in line:
            delim = None


def _py_files():
    for p in PKG.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def _next_code_line(lines, idx):
    j = idx + 1
    while j < len(lines) and not lines[j][1].strip():
        j += 1
    return j


class TestNoSilentSwallow:
    def test_no_silent_except_actions(self):
        """No broad `except Exception:` may end in pass/continue (commented or
        not) — it must log. Regression: the v1.5.0 check only matched a bare
        `pass`, so `pass  # comment` and `continue` slipped through."""
        offenders = []
        for p in _py_files():
            lines = list(_iter_code_lines(p))
            for idx, (lineno, line) in enumerate(lines):
                if _EXC.match(line):
                    j = _next_code_line(lines, idx)
                    if j < len(lines) and _SILENT.match(lines[j][1].strip()):
                        offenders.append(f"{p.relative_to(ROOT)}:{lineno}")
        assert not offenders, (
            "silent broad-exception swallows (must log): " + ", ".join(offenders))

    def test_every_swallow_logs(self):
        """Each converted node must call logger.debug (so it is observable)."""
        n = 0
        for p in _py_files():
            lines = list(_iter_code_lines(p))
            for i, (_, line) in enumerate(lines):
                if _EXC.match(line):
                    j = _next_code_line(lines, i)
                    if j < len(lines) and "logger.debug(" in lines[j][1]:
                        n += 1
        assert n >= 20, f"expected >=20 logged swallows, found {n}"

    def test_no_bare_except(self):
        """A bare `except:` disables even KeyboardInterrupt — never allowed."""
        offenders = []
        for p in _py_files():
            for i, line in _iter_code_lines(p):
                if _BARE.match(line):
                    offenders.append(f"{p.relative_to(ROOT)}:{i}")
        assert not offenders, "bare except: " + ", ".join(offenders)


class TestSwallowIsObservable:
    def test_repo_map_injection_failure_is_logged(self, tmp_path, monkeypatch, caplog):
        monkeypatch.chdir(tmp_path)
        from zouwucode.tui.app import ZOUWUCODEApp
        import zouwucode.repo_map as rm

        app = ZOUWUCODEApp()
        app.config.context.repo_map_enabled = True

        def _boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(rm, "build_repo_map", _boom)
        with caplog.at_level(logging.DEBUG, logger="zouwucode.tui.app"):
            app._build_system_prompt()          # must not raise
        assert any("swallowed" in r.getMessage() for r in caplog.records), \
            "swallowed error was not logged"

    def test_module_dispatch_failure_is_logged(self, caplog):
        """A broken module must not swallow the message NOR its own error."""
        import asyncio

        from zouwucode.modules.manager import ModuleManager

        class _Broken:
            name = "broken-mod"

            async def handle_message(self, text):
                raise RuntimeError("module exploded")

        mgr = ModuleManager()
        mgr.register(_Broken())
        with caplog.at_level(logging.WARNING, logger="zouwucode.modules"):
            result = asyncio.run(mgr.dispatch_message("hi"))
        assert result is None, "broken module must fall through"
        assert any("broken-mod" in r.getMessage() for r in caplog.records), \
            "module failure was not logged"