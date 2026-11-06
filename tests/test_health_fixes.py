"""Regression tests for the workspace health-check fixes.

Pins four defects found during the audit:

1. ``textual`` was imported eagerly by ``zouwucode.tui``, so a base install
   could not run ``--version`` / CLI / ``--web`` without the TUI extra.
2. grep output deviated from the documented ``path:line:text`` contract on
   Windows (``.\\src\\util.py``), and single-file searches dropped the
   filename when ripgrep handled them.
3. ``install.ps1`` launched ``zouwucode/__main__.py`` directly, which breaks
   relative imports; it must use ``python -m zouwucode``.
4. Editable installs kept stale metadata, so ``pip show`` disagreed with
   ``zouwucode.__version__``.
"""

from __future__ import annotations

import asyncio
import importlib.metadata as md
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run_cli(args: list[str], cwd: Path, *, block_textual: bool = True):
    """Run ``python -m zouwucode`` in a subprocess, optionally without textual."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"]
                                     if env.get("PYTHONPATH") else "")
    code = (
        "import runpy, sys;"
        + ("sys.modules['textual'] = None;" if block_textual else "")
        + f"sys.argv = ['zouwucode', {', '.join(repr(a) for a in args)}];"
        "runpy.run_module('zouwucode', run_name='__main__')"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120,
    )


class TestBaseInstallWithoutTextual:
    """The TUI extra must stay optional; only ``--tui`` may require it."""

    def test_version_works_without_textual(self, tmp_path):
        r = _run_cli(["--version"], tmp_path)
        assert r.returncode == 0, r.stderr
        assert "ZOUWUCODE" in r.stdout

    def test_help_works_without_textual(self, tmp_path):
        r = _run_cli(["--help"], tmp_path)
        assert r.returncode == 0, r.stderr
        assert "--web" in r.stdout

    def test_cli_imports_without_textual(self, tmp_path):
        code = (
            "import sys; sys.modules['textual'] = None;"
            "from zouwucode.tui.app import ZOUWUCODEApp;"
            "assert ZOUWUCODEApp is not None"
        )
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        r = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path),
                           env=env, capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr

    def test_tui_prints_actionable_hint(self, tmp_path):
        r = _run_cli(["--tui"], tmp_path)
        assert r.returncode == 2, r.stderr
        combined = r.stdout + r.stderr
        assert "pip install" in combined
        assert "textual" in combined
        assert "Traceback" not in combined


class TestGrepOutputContract:
    """Both grep engines must emit the same ``path:line:text`` shape."""

    @pytest.mark.parametrize("raw, expected", [
        (".\\src\\util.py:12:VALUE = 42", "src/util.py:12:VALUE = 42"),
        ("./src/util.py:12:VALUE = 42", "src/util.py:12:VALUE = 42"),
        ("src/util.py:12:VALUE = 42", "src/util.py:12:VALUE = 42"),
    ])
    def test_relative_line_is_posix(self, raw, expected):
        from zouwucode.tools.search_tools import _normalize_rel_path
        assert _normalize_rel_path(raw) == expected

    def test_single_file_gets_filename(self):
        from zouwucode.tools.search_tools import _normalize_rel_path
        assert _normalize_rel_path("12:VALUE = 42", "util.py") == "util.py:12:VALUE = 42"

    def test_files_only_output_keeps_path_only(self):
        from zouwucode.tools.search_tools import _normalize_rel_path
        assert _normalize_rel_path(".\\src\\util.py") == "src/util.py"

    def test_matched_text_keeps_its_backslashes(self):
        """Only the path portion is rewritten — matches may contain ``\\``."""
        from zouwucode.tools.search_tools import _normalize_rel_path
        raw = ".\\src\\a.py:3:C:\\tmp\\x"
        assert _normalize_rel_path(raw) == "src/a.py:3:C:\\tmp\\x"

    def test_python_fallback_matches_contract(self, tmp_path):
        from zouwucode.tools.search_tools import GrepTool
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        r = asyncio.run(GrepTool().execute(pattern="VALUE", path=str(tmp_path / "src" / "app.py")))
        assert r.success
        assert "app.py:1:" in r.output

    @pytest.mark.skipif(shutil.which("rg") is None,
                        reason="ripgrep not installed on this host")
    def test_ripgrep_matches_contract(self, tmp_path):
        from zouwucode.tools.search_tools import GrepTool
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "util.py").write_text("VALUE = 42\n", encoding="utf-8")
        r = asyncio.run(GrepTool().execute(pattern="VALUE", path=str(tmp_path)))
        assert r.success
        assert "src/util.py:1:" in r.output
        assert ".\\" not in r.output
        single = asyncio.run(GrepTool().execute(pattern="VALUE", path=str(tmp_path / "src" / "util.py")))
        assert single.success
        assert "util.py:1:" in single.output

    def test_disable_rg_env_skips_ripgrep(self, tmp_path, monkeypatch):
        """CI's "no ripgrep" leg uses this switch — the Python engine must win."""
        from zouwucode.tools import search_tools
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "util.py").write_text("VALUE = 42\n", encoding="utf-8")
        monkeypatch.setenv("ZOUWUCODE_DISABLE_RG", "1")
        looked_up = []
        real_which = search_tools.shutil.which
        monkeypatch.setattr(search_tools.shutil, "which",
                            lambda name: looked_up.append(name) or real_which(name))
        r = asyncio.run(search_tools.GrepTool().execute(
            pattern="VALUE", path=str(tmp_path)))
        assert r.success and "src/util.py:1:" in r.output
        assert "rg" not in looked_up


class TestInstaller:
    def test_installer_uses_module_entrypoint(self):
        src = (ROOT / "install.ps1").read_text(encoding="utf-8")
        assert "__main__.py" not in src, "直接执行 __main__.py 会炸相对导入"
        assert "python -m zouwucode @args" in src


class TestInstalledMetadata:
    def test_metadata_version_matches_source(self):
        import zouwucode
        try:
            dist_version = md.version("zouwucode")
        except md.PackageNotFoundError:
            pytest.skip("zouwucode is not pip-installed in this environment")
        assert dist_version == zouwucode.__version__, (
            "editable 安装元数据过期：pip install -e . 后重试"
        )
