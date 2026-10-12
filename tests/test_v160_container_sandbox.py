"""Regression for v1.6.0: opt-in container sandbox (real isolation).

The regex blacklist is policy, not isolation. When enabled AND a runtime is
present, shell commands run inside a throwaway container (workspace mounted,
no network); otherwise the tool degrades to the host shell. These tests use a
fake runtime so they never need docker.
"""

import asyncio
from pathlib import Path

import pytest

from zouwucode.sandbox.container import ContainerSandbox
from zouwucode.config import ZOUWUCODEConfig
from zouwucode.tools.shell_tools import ShellTool


class TestContainerSandbox:
    def test_detect_none_when_absent(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert ContainerSandbox.detect() == ""

    def test_detect_prefers_explicit_then_docker(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name
                            if name in ("docker", "podman") else None)
        assert ContainerSandbox.detect("podman") == "podman"
        assert ContainerSandbox.detect() == "docker"

    def test_build_argv_isolation_flags(self, tmp_path):
        cs = ContainerSandbox(tmp_path, image="alpine", runtime="docker")
        argv = cs.build_argv("echo hi")
        assert argv[:2] == ["docker", "run"]
        assert "--rm" in argv
        assert f"{tmp_path}:{cs.mount}" in " ".join(argv)
        assert "--network" in argv and "none" in argv      # network off by default
        assert argv[-3:] == ["alpine", "sh", "-lc", "echo hi"][-4:] or argv[-1] == "echo hi"

    def test_network_can_be_enabled(self, tmp_path):
        cs = ContainerSandbox(tmp_path, runtime="docker", network=True)
        assert "--network" not in cs.build_argv("x")

    def test_workdir_maps_inside_mount(self, tmp_path):
        sub = tmp_path / "pkg"
        sub.mkdir()
        cs = ContainerSandbox(tmp_path, runtime="docker")
        argv = cs.build_argv("ls", workdir=sub)
        wd = argv[argv.index("-w") + 1]
        assert wd.startswith(cs.mount) and "pkg" in wd

    def test_host_workdir_is_not_leaked(self, tmp_path):
        cs = ContainerSandbox(tmp_path, runtime="docker")
        argv = cs.build_argv("ls", workdir=Path("C:/Windows") if Path("C:/Windows").exists() else tmp_path)
        wd = argv[argv.index("-w") + 1]
        assert wd.startswith(cs.mount)

    def test_unavailable_without_workspace(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/docker")
        assert ContainerSandbox(None, runtime="docker").available() is False


class _FakeContainer:
    def __init__(self, out=b"hi", err=b"", code=0, raise_exc=None):
        self.image = "fake:latest"
        self._r = (out, err, code)
        self._raise = raise_exc
        self.calls = 0

    def available(self):
        return True

    async def run(self, command, timeout=30, workdir=None):
        self.calls += 1
        if self._raise:
            raise self._raise
        return self._r


class TestShellToolRouting:
    def test_routes_to_container_when_available(self):
        fc = _FakeContainer(out=b"inside container")
        tool = ShellTool(sandbox=None, container=fc)
        assert tool._use_container() is True
        r = asyncio.run(tool.execute("echo hi"))
        assert r.success and "inside container" in r.output
        assert fc.calls == 1

    def test_container_nonzero_exit_is_error(self):
        tool = ShellTool(sandbox=None, container=_FakeContainer(out=b"", err=b"boom", code=2))
        r = asyncio.run(tool.execute("false"))
        assert not r.success and "boom" in (r.error or "")

    def test_no_container_means_host(self):
        assert ShellTool(sandbox=None).container is None
        assert ShellTool(sandbox=None)._use_container() is False

    def test_runtime_failure_falls_back_and_disables(self, monkeypatch):
        tool = ShellTool(sandbox=None, container=_FakeContainer(raise_exc=RuntimeError("no daemon")))
        # make the host path fail fast so no real command runs in the test
        monkeypatch.setattr(tool, "_pick_executable",
                            lambda cmd: (_ for _ in ()).throw(RuntimeError("host too")))
        r = asyncio.run(tool.execute("echo hi"))
        assert not r.success
        assert tool.container is None            # broken runtime is not retried


class TestWiring:
    def test_default_is_off(self):
        assert ZOUWUCODEConfig().sandbox.container is False

    def test_builtin_tools_wire_container_when_enabled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from zouwucode.runtime import _make_container
        from zouwucode.sandbox.permission import PermissionManager

        cfg = ZOUWUCODEConfig()
        sandbox = PermissionManager(cfg.sandbox)
        sandbox.set_workspace(tmp_path)

        assert _make_container(sandbox, cfg) is None            # off by default
        cfg.sandbox.container = True
        cs = _make_container(sandbox, cfg)
        assert cs is not None and cs.workspace == tmp_path.resolve()
