"""Regression for v1.5.0: observability hardening.

The project's own audit called "silent failure" (a failing path that reports
success / swallows the error) the most dangerous bug class. This locks the
rule in: a broad `except Exception:` must never end in a bare `pass` — it must
log. These tests are static (guard against regression) plus one behavioural
check that a swallowed error actually reaches the log.
"""

import logging
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "zouwucode"

_EXC = re.compile(r"^(\s*)except\s+Exception(\s+as\s+\w+)?\s*:(\s*#.*)?$")


def _py_files():
    for p in PKG.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


class TestNoSilentSwallow:
    def test_no_broad_except_pass(self):
        """No `except Exception:` may be followed by a bare `pass`."""
        offenders = []
        for p in _py_files():
            lines = p.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines):
                if _EXC.match(line):
                    j = i + 1
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    if j < len(lines) and lines[j].strip() == "pass":
                        offenders.append(f"{p.relative_to(ROOT)}:{i + 1}")
        assert not offenders, "silent broad-exception swallows: " + ", ".join(offenders)

    def test_every_swallow_logs(self):
        """Each converted node must call logger.debug (so it is observable)."""
        n = 0
        for p in _py_files():
            src = p.read_text(encoding="utf-8")
            lines = src.splitlines()
            for i, line in enumerate(lines):
                if _EXC.match(line):
                    j = i + 1
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    if j < len(lines) and "logger.debug(" in lines[j]:
                        n += 1
        assert n >= 20, f"expected >=20 logged swallows, found {n}"

    def test_no_bare_except(self):
        """A bare `except:` disables even KeyboardInterrupt — never allowed."""
        offenders = []
        for p in _py_files():
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines()):
                if re.match(r"^\s*except\s*:\s*(#.*)?$", line):
                    offenders.append(f"{p.relative_to(ROOT)}:{i + 1}")
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
