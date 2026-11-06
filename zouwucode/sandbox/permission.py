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
    # Token-scoped on purpose: broad substrings here caused false blocks on
    # benign commands (PowerShell's `Format-Table` matched "format"; a path
    # containing "codebuddy" matched "dd") and burned model rounds.
    (r'\bmkfs(\.[a-z0-9]+)?\b', "Filesystem formatting (mkfs)"),
    (r'(?<![\w-])format\s+[a-z]:', "Filesystem formatting (format <drive>:)"),
    (r'\bdd\s+(if|of)=', "Disk imaging (dd)"),
    (r'\bfdisk\b', "Partition editing (fdisk)"),
    (r'\b(sudo|runas)\b|\bsu\s+-\w', "Privilege escalation"),
    (r'(chmod|chown|attrib)\s+.*777', "Permission abuse"),
    (r'(>|>>)\s+/dev/', "Device overwrite"),
    (r'(wget|curl)\s+.*\|\s*(bash|sh|powershell)', "Pipe-to-shell"),
    (r'(cmd|powershell)\s+.*-EncodedCommand', "Encoded command execution"),
    (r'(Invoke-Expression|iex)\s', "PowerShell IEX"),
    (r'(Start-Process|Invoke-Item)\s', "PowerShell process start"),
    (r'\breg(\.exe)?\s+(add|delete|query|import|export|save|load|copy|'
     r'restore|compare)\b|\bregedit\b', "Registry modification"),
]

# ── Zero-width character injection patterns ──────────────────────────────────
ZERO_WIDTH_CHARS = re.compile(
    '[\u200b\u200c\u200d\u2060\u2061\u2062\u2063\u2064\uFEFF]'
)

# Deletion commands whose short flags may be split (`rm -r -f`) or spelled
# long (`rm --recursive --force`). The old regex only caught `-rf /` and
# `-rf *`, so `rm -r -f ./build` slipped through — treat every forced
# recursive delete as destructive regardless of the target.
_DELETE_COMMAND = re.compile(r'\b(rm|del|remove|unlink)\b', re.IGNORECASE)


def _forced_recursive_delete(command: str) -> bool:
    """True when *command* contains a delete with both recursive+force flags."""
    for match in _DELETE_COMMAND.finditer(command):
        tokens = command[match.end():].split()
        recursive = forced = False
        for token in tokens:
            if not token.startswith("-"):
                break  # flags precede the operand
            flag = token.lstrip("-").lower()
            if token.startswith("--"):
                recursive = recursive or flag == "recursive"
                forced = forced or flag == "force"
            else:
                # Short flags may be combined (-rf / -fr / -Rf).
                recursive = recursive or "r" in flag
                forced = forced or "f" in flag
        if recursive and forced:
            return True
    return False


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
        2. Forced recursive deletion (``rm -r -f`` / ``--recursive --force``)
        3. Dangerous pattern matching
        4. Blocklist checking
        5. Allowlist checking
        """
        return self.block_reason(command) == ""

    def block_reason(self, command: str) -> str:
        """Explain the first matching sandbox rule, or ``""`` when allowed."""
        if not self.config.enabled:
            return ""

        # Layer 1: Zero-width space injection
        if ZERO_WIDTH_CHARS.search(command):
            return "Zero-width character injection"

        # Layer 2: Forced recursive deletion in any flag spelling
        if _forced_recursive_delete(command):
            return "Recursive forced deletion (rm -r -f)"

        # Layer 3: Dangerous patterns
        for pattern, description in DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return description

        # Layer 4: Blocklist
        for blocked in self._blocklist:
            if blocked.lower() in command.lower():
                return f"Command matches blocklist entry {blocked!r}"

        # Layer 5: If allowlist is set, only allowlisted commands pass
        if self._allowlist:
            cmd_name = command.strip().split()[0].lower() if command.strip() else ""
            if cmd_name not in self._allowlist:
                return f"Command {cmd_name or '<empty>'!r} is not in the allowlist"

        return ""

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