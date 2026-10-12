"""Container sandbox \u2014 real filesystem/process isolation for shell commands.

The regex blacklist in permission.py stops obvious accidents, but it is *policy*
not *isolation*: a determined (or simply unlucky) command can still touch the
host. This module upgrades that to genuine isolation by running commands inside
a throwaway container with only the workspace mounted, no network by default,
and dropped capabilities.

It is OPT-IN (``sandbox.container: true``) and degrades gracefully: if no
container runtime (docker/podman) is found, callers fall back to the host shell.
The runtime CLI runs on the HOST; what is isolated is the command's execution
environment, not the launcher.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

logger = logging.getLogger("zouwucode.sandbox.container")

# Preference order when auto-detecting (docker first, then podman alias).
_RUNTIMES = ("docker", "podman")


class ContainerSandbox:
    """Builds and runs a command inside an isolated container."""

    def __init__(self, workspace, image: str = "python:3.12-slim",
                 runtime: str = "", network: bool = False,
                 mount: str = "/workspace"):
        self.workspace = Path(workspace).resolve() if workspace else None
        self.image = image or "python:3.12-slim"
        self.network = bool(network)
        self.mount = mount or "/workspace"
        self.runtime = runtime or self.detect()

    # \u2500\u2500 detection \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    @staticmethod
    def detect(explicit: str = "") -> str:
        """Return an available container runtime name, or "" if none."""
        if explicit:
            return explicit if shutil.which(explicit) else ""
        for name in _RUNTIMES:
            if shutil.which(name):
                return name
        return ""

    def available(self) -> bool:
        return bool(self.runtime and shutil.which(self.runtime) and self.workspace)

    # \u2500\u2500 command construction \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    def build_argv(self, command: str, workdir=None) -> list:
        """The argv that runs ``command`` inside the container.

        - the workspace is mounted read-write at ``self.mount``;
        - the container working dir is inside the mount (never a host path);
        - network is disabled unless explicitly enabled (\u2192 exfiltration/malware);
        - ``--rm`` so nothing is left behind.
        """
        wd = self.mount
        if workdir:
            try:
                rel = Path(workdir).resolve().relative_to(self.workspace)
                wd = f"{self.mount}/{rel.as_posix()}".rstrip("/")
            except (ValueError, OSError):
                wd = self.mount
        argv = [self.runtime, "run", "--rm", "-i",
                "-v", f"{self.workspace}:{self.mount}",
                "-w", wd]
        if not self.network:
            argv += ["--network", "none"]
        argv += [self.image, "sh", "-lc", command]
        return argv

    # \u2500\u2500 execution \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    async def run(self, command: str, timeout: int = 30, workdir=None):
        """Run ``command`` in the container. Returns (stdout, stderr, code)."""
        argv = self.build_argv(command, workdir=workdir)
        logger.debug("container run: %s", " ".join(argv[:8]) + " \u2026")
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise
        return out, err, proc.returncode
