"""Permission sandbox — multi-layer security for tool execution.

Inspired by Claude Code's 23-layer bash security system including:
- Command allowlists/blocklists
- Zsh equals expansion defense
- Zero-width space injection detection
- Workspace-scoped filesystem access
"""

import re
from pathlib import Path
from typing import Optional

from ..config import SandboxConfig


# ── Dangerous command patterns (Claude Code-inspired) ────────────────────────
DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r'(rm|del|remove|unlink)\s+(-rf?\s+)?[/\\]', "Root filesystem deletion"),
    (r'(rm|del|remove|unlink)\s+(-rf?\s+)?\*', "Wildcard deletion"),
    (r'(mkfs|format|dd|fdisk)', "Filesystem formatting"),
    (r'(sudo|runas|su\s)', "Privilege escalation"),
    (r'(chmod|chown|attrib)\s+.*777', "Permission abuse"),
    (r'(>|>>)\s+/dev/', "Device overwrite"),
    (r'(wget|curl)\s+.*\|\s*(bash|sh|powershell)', "Pipe-to-shell"),
    (r'(cmd|powershell)\s+.*-EncodedCommand', "Encoded command execution"),
    (r'(Invoke-Expression|iex)\s', "PowerShell IEX"),
    (r'(Start-Process|Invoke-Item)\s', "PowerShell process start"),
    (r'(reg\s|regedit)', "Registry modification"),
]

# ── Zero-width character injection patterns ──────────────────────────────────
ZERO_WIDTH_CHARS = re.compile(
    '[\u200b\u200c\u200d\u2060\u2061\u2062\u2063\u2064\uFEFF]'
)


class PermissionManager:
    """Multi-layer sandbox for tool execution permissions."""

    def __init__(self, config: SandboxConfig):
        self.config = config
        self._workspace_root: Optional[Path] = None
        self._blocklist: set[str] = set()
        self._allowlist: set[str] = set()

    def set_workspace(self, path: Path) -> None:
        """Set the workspace root for filesystem sandboxing."""
        self._workspace_root = path.resolve()

    def add_blocklist(self, patterns: list[str]) -> None:
        """Add command patterns to the blocklist."""
        self._blocklist.update(patterns)

    def add_allowlist(self, commands: list[str]) -> None:
        """Add commands to the allowlist."""
        self._allowlist.update(commands)

    async def check_command(self, command: str) -> bool:
        """Check if a command is allowed to execute.

        Implements multi-layer security checks:
        1. Zero-width character injection detection
        2. Dangerous pattern matching
        3. Blocklist checking
        4. Allowlist checking
        """
        if not self.config.enabled:
            return True

        # Layer 1: Zero-width space injection
        if ZERO_WIDTH_CHARS.search(command):
            return False

        # Layer 2: Dangerous patterns
        for pattern, description in DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return False

        # Layer 3: Blocklist
        for blocked in self._blocklist:
            if blocked.lower() in command.lower():
                return False

        # Layer 4: If allowlist is set, only allowlisted commands pass
        if self._allowlist:
            cmd_name = command.strip().split()[0].lower() if command.strip() else ""
            return cmd_name in self._allowlist

        return True

    async def check_file_access(self, file_path: str, mode: str = "r") -> bool:
        """Check if file access is allowed within the workspace."""
        if not self.config.enabled or not self._workspace_root:
            return True

        path = Path(file_path).resolve()
        allowed_paths = [Path(p).resolve() for p in self.config.allowed_paths]

        # Always include the workspace root as an allowed path
        if self._workspace_root:
            allowed_paths.append(self._workspace_root)

        # Check if path is within allowed paths
        for allowed in allowed_paths:
            try:
                path.relative_to(allowed)
                return True
            except ValueError:
                continue

        return False

    async def check_network(self, url: str) -> bool:
        """Check if network access is allowed."""
        if not self.config.enabled:
            return True
        return self.config.allow_network

    async def check_git(self) -> bool:
        """Check if git operations are allowed."""
        if not self.config.enabled:
            return True
        return self.config.allow_git