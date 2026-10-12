"""Regression: workspace rollback (session.rollback_enabled).

Pins the feature SessionManager's own docstring promised from the start
("save, resume, fork, and rollback") but never implemented. Backed by the
side-git WorkspaceSnapshotter: snapshots live in a separate GIT_DIR whose
work tree is the workspace, so the project's own .git is never touched.

Covered:
- a snapshot restores modified files and removes files added after it
- consecutive rollbacks move further back (history is truncated, not stacked)
- rollback is a safe no-op when disabled / git is missing
- the project's own .git and node_modules/__pycache__ are excluded
- SessionManager wires the config knob and snapshots per turn
"""

import subprocess

import pytest

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.session.manager import SessionManager
from zouwucode.session.snapshot import WorkspaceSnapshotter


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestSnapshotter:
    def _snap(self, tmp_path):
        return WorkspaceSnapshotter(tmp_path, tmp_path.parent / "snap" / "ws.git")

    def test_available_true_in_git_repo(self, tmp_path):
        assert self._snap(tmp_path).available is True

    def test_rollback_restores_and_removes(self, tmp_path):
        s = self._snap(tmp_path)
        _write(tmp_path / "a.txt", "v1")
        assert s.snapshot("session start")
        _write(tmp_path / "a.txt", "v2")
        _write(tmp_path / "b.txt", "new")
        assert s.snapshot("turn 1")
        r = s.rollback(1)
        assert r["ok"], r
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "v1"
        assert not (tmp_path / "b.txt").exists()
        assert "b.txt" in r["removed"]

    def test_consecutive_rollbacks_move_further_back(self, tmp_path):
        s = self._snap(tmp_path)
        _write(tmp_path / "a.txt", "v1")
        s.snapshot("S0")
        _write(tmp_path / "a.txt", "v2")
        _write(tmp_path / "b.txt", "b")
        s.snapshot("S1")
        _write(tmp_path / "c.txt", "c")
        s.snapshot("S2")
        assert s.rollback(1)["ok"]
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "v2"
        assert (tmp_path / "b.txt").exists()
        assert not (tmp_path / "c.txt").exists()
        assert s.rollback(1)["ok"]        # now undoes the earlier turn
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "v1"
        assert not (tmp_path / "b.txt").exists()

    def test_too_many_steps_is_refused(self, tmp_path):
        s = self._snap(tmp_path)
        _write(tmp_path / "a.txt", "v1")
        s.snapshot("S0")
        r = s.rollback(5)
        assert not r["ok"]
        assert "not enough snapshots" in r["error"]

    def test_disabled_is_a_safe_noop(self, tmp_path):
        s = WorkspaceSnapshotter(tmp_path, tmp_path.parent / "s.git", enabled=False)
        assert s.available is False
        assert s.snapshot("x") is None
        assert s.rollback(1)["ok"] is False

    def test_project_git_and_caches_excluded(self, tmp_path):
        s = self._snap(tmp_path)
        _write(tmp_path / ".git" / "HEAD", "ref: refs/heads/main")
        _write(tmp_path / "node_modules" / "x.js", "junk")
        _write(tmp_path / "__pycache__" / "x.pyc", "junk")
        _write(tmp_path / "src" / "main.py", "print(1)")
        s.snapshot("S0")
        tracked = s._git("ls-files").stdout
        assert "main.py" in tracked
        assert "node_modules" not in tracked
        assert "__pycache__" not in tracked
        assert ".git/HEAD" not in tracked
        # the project's own .git is untouched
        assert (tmp_path / ".git" / "HEAD").read_text(encoding="utf-8").startswith("ref:")


class TestSessionManagerWiring:
    def _manager(self, tmp_path, monkeypatch, enabled=True):
        monkeypatch.chdir(tmp_path)
        cfg = ZOUWUCODEConfig()
        cfg.data_dir = str(tmp_path / "data")
        cfg.session.rollback_enabled = enabled
        return SessionManager(cfg)

    def test_snapshotter_built_from_config(self, tmp_path, monkeypatch):
        m = self._manager(tmp_path, monkeypatch, enabled=True)
        assert m.snapshotter is not None
        assert m.snapshotter.enabled is True

    def test_disabled_config_yields_noop_snapshotter(self, tmp_path, monkeypatch):
        m = self._manager(tmp_path, monkeypatch, enabled=False)
        assert m.snapshotter is not None
        assert m.snapshotter.enabled is False
        assert m.snapshot_workspace("x") is None

    def test_turn_takes_a_snapshot(self, tmp_path, monkeypatch):
        m = self._manager(tmp_path, monkeypatch, enabled=True)
        m.start_session("s-1")
        (tmp_path / "code.py").write_text("print(1)", encoding="utf-8")
        m.log_turn({"role": "user", "content": "hi"})
        assert m.snapshotter.count() >= 1
        # the changed file is inside the side-repo history
        assert "code.py" in m.snapshotter._git("ls-files").stdout

    def test_rollback_workspace_undoes_a_turn(self, tmp_path, monkeypatch):
        m = self._manager(tmp_path, monkeypatch, enabled=True)
        m.start_session("s-1")
        (tmp_path / "a.txt").write_text("v1", encoding="utf-8")
        m.snapshot_workspace("baseline")
        (tmp_path / "a.txt").write_text("v2", encoding="utf-8")
        m.log_turn({"role": "assistant", "content": "did work"})
        r = m.rollback_workspace(1)
        assert r["ok"], r
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "v1"


class TestConfigDefaults:
    def test_default_rollback_enabled(self):
        assert ZOUWUCODEConfig().session.rollback_enabled is True

    def test_default_stats_window(self):
        assert ZOUWUCODEConfig().cache.stats_window == 50
