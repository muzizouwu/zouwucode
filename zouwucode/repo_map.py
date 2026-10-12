"""Repo map — a compact, ranked overview of the codebase (Aider-style).

Dumping the whole tree into context is expensive and noisy. Instead we list
only the SIGNATURES (classes / functions / top-level defs) of the most
*referenced* files, ranked by how often a file's name is mentioned elsewhere.
That gives the model a repo-wide picture inside a small token budget, so it
spends fewer rounds exploring — the single biggest lever on cost and latency.
"""

import os
import re
from pathlib import Path

from .tokens import estimate_tokens

IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "zouwucode_data", ".zouwucode_worktrees", ".hello-my-zouwucode",
    ".idea", ".vscode", "target", "vendor",
}

CODE_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".rb", ".php", ".cs", ".swift",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".scala", ".sh",
}

# Language-agnostic "definition" line patterns (first match wins per line).
DEF_PATTERNS = [
    ("py", re.compile(r"^\s*(?:async\s+)?def\s+(\w+)")),
    ("py", re.compile(r"^\s*class\s+(\w+)")),
    ("ts", re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)")),
    ("ts", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)")),
    ("ts", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(")),
    ("ts", re.compile(r"^\s*(?:export\s+)?(?:interface|enum|type)\s+(\w+)")),
    ("go", re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)")),
    ("go", re.compile(r"^\s*type\s+(\w+)\s+struct")),
    ("rs", re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)")),
    ("rs", re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)")),
    ("java", re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:final\s+)?class\s+(\w+)")),
    ("java", re.compile(r"^\s*(?:public|private|protected)\s+(?:static\s+)?[\w<>\[\]]+\s+(\w+)\s*\(")),
]


def _extract_defs(path: Path, limit: int = 40):
    defs = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return defs
    for line in text.splitlines():
        if line[:1] in (" ", "\t") and "def " not in line and "class " not in line                 and "fn " not in line and "func " not in line and "function " not in line:
            continue
        for _lang, pat in DEF_PATTERNS:
            m = pat.match(line)
            if m:
                defs.append(m.group(1))
                break
        if len(defs) >= limit:
            break
    return defs


def _source_files(root: Path):
    files = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in IGNORE_DIRS and not d.startswith(".")]
        for f in fn:
            if os.path.splitext(f)[1].lower() in CODE_EXTS:
                files.append(Path(dp) / f)
    return files


def build_repo_map(root=".", max_tokens: int = 1024, max_files: int = 60,
                   max_defs: int = 18) -> str:
    """Return a compact repo map: ranked files + their top-level signatures."""
    root = Path(root).resolve()
    files = _source_files(root)[:3000]
    if not files:
        return ""

    # Rank files by how often their stem is referenced in OTHER files
    # (a cheap PageRank substitute). Bigger, more-referenced files first.
    blobs = {}
    for p in files:
        try:
            blobs[p] = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            blobs[p] = ""

    def ref_count(p: Path) -> int:
        stem = p.stem
        if len(stem) < 3:
            return 0
        rx = re.compile(r"\b" + re.escape(stem) + r"\b")
        return sum(1 for q, b in blobs.items() if q != p and rx.search(b))

    ranked = sorted(files, key=lambda p: (ref_count(p), len(blobs.get(p, ""))),
                    reverse=True)

    lines, used, shown = [], 0, 0
    for p in ranked:
        rel = str(p.relative_to(root)).replace("\\", "/")
        defs = _extract_defs(p, limit=max_defs)
        block = rel + (("\n" + "\n".join("  " + d for d in defs)) if defs else "")
        cost = estimate_tokens(block)
        if used + cost > max_tokens and lines:
            break
        used += cost
        lines.append(block)
        shown += 1
        if shown >= max_files:
            break
    if not lines:
        return ""
    return ("Repo map (%d files shown, ranked by cross-references; "
            "signatures only):\n" % shown) + "\n".join(lines)


def repo_map_enabled(config) -> bool:
    ctx = getattr(config, "context", None)
    return bool(getattr(ctx, "repo_map_enabled", False))


def repo_map_max_tokens(config) -> int:
    ctx = getattr(config, "context", None)
    return int(getattr(ctx, "repo_map_max_tokens", 1024) or 1024)
