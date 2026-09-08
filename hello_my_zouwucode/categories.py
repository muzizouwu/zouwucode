"""Category system — semantic task routing (ported from oh-my-opencode).

Instead of picking a model name, orchestrators pick a *category* that
describes the intent of a task (`ultrabrain`, `deep`, `quick`, ...).
The category automatically maps to the right model configuration.

ZOUWUCODE speaks to a single DeepSeek provider, so category → model
routing is expressed through the provider's reasoning-effort knobs:
  ultrabrain / deep   → higher reasoning effort
  quick / writing     → low (fast) reasoning effort
This mirrors oh-my-opencode's category model-matching while staying
native to ZOUWUCODE's single-provider architecture.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ── Category Registry ─────────────────────────────────────────────────────────
# Ported from oh-my-opencode `delegate-task` category names + built-in defaults.
CATEGORY_NAMES: tuple[str, ...] = (
    "visual-engineering",
    "artistry",
    "ultrabrain",
    "deep",
    "quick",
    "unspecified-low",
    "unspecified-high",
    "writing",
    "quick-rust",
    "quick-zig",
    "git",
)


@dataclass
class CategorySpec:
    """A semantic task category and its model-routing settings."""

    name: str
    description: str
    reasoning_intensity: str  # low | medium | max  → maps to DeepSeek reasoning_effort
    temperature: float = 0.0
    # Recommended use cases — surfaced to the orchestrator prompt
    use_for: str = ""
    # Skills that should be auto-loaded with this category (zouwucode skill names)
    load_skills: tuple[str, ...] = field(default_factory=tuple)


# Built-in defaults — ported from oh-my-opencode category defaults,
# adapted to DeepSeek's three-tier reasoning effort.
CATEGORY_DEFAULTS: dict[str, CategorySpec] = {
    "visual-engineering": CategorySpec(
        name="visual-engineering",
        description="Frontend / UI / visual work. Prefers a strong, careful model.",
        reasoning_intensity="max",
        temperature=0.1,
        use_for="前端/UI 开发、视觉验证、布局与样式",
        load_skills=("frontend",),
    ),
    "artistry": CategorySpec(
        name="artistry",
        description="Creative and design work. Higher temperature for variety.",
        reasoning_intensity="max",
        temperature=0.4,
        use_for="创意设计、文案润色、界面美化",
    ),
    "ultrabrain": CategorySpec(
        name="ultrabrain",
        description="Hardest logic and architecture. Deepest reasoning.",
        reasoning_intensity="max",
        temperature=0.0,
        use_for="复杂架构、算法难题、疑难调试、规划",
    ),
    "deep": CategorySpec(
        name="deep",
        description="General strong model for autonomous implementation.",
        reasoning_intensity="medium",
        temperature=0.0,
        use_for="实际编码实现、多文件改动、完整功能开发",
    ),
    "quick": CategorySpec(
        name="quick",
        description="Fast and cheap. Best for grep, search, summaries.",
        reasoning_intensity="low",
        temperature=0.0,
        use_for="快速检索、符号定位、简单问答、Todo 更新",
    ),
    "unspecified-low": CategorySpec(
        name="unspecified-low",
        description="Low-effort fallback when no category fits.",
        reasoning_intensity="low",
        temperature=0.0,
        use_for="未分类的轻量任务",
    ),
    "unspecified-high": CategorySpec(
        name="unspecified-high",
        description="High-effort fallback when no category fits.",
        reasoning_intensity="max",
        temperature=0.0,
        use_for="未分类的重型任务",
    ),
    "writing": CategorySpec(
        name="writing",
        description="Prose and documentation work.",
        reasoning_intensity="low",
        temperature=0.4,
        use_for="文档撰写、注释优化、报告输出",
    ),
    "quick-rust": CategorySpec(
        name="quick-rust",
        description="Quick tasks scoped to Rust codebases.",
        reasoning_intensity="low",
        temperature=0.0,
        use_for="Rust 快速任务",
    ),
    "quick-zig": CategorySpec(
        name="quick-zig",
        description="Quick tasks scoped to Zig codebases.",
        reasoning_intensity="low",
        temperature=0.0,
        use_for="Zig 快速任务",
    ),
    "git": CategorySpec(
        name="git",
        description="Git operations: status, diff, commit, log.",
        reasoning_intensity="low",
        temperature=0.0,
        use_for="Git 操作、提交、历史查询",
    ),
}


def get_category(name: str) -> CategorySpec:
    """Return the category spec, or `unspecified-high` if unknown."""
    return CATEGORY_DEFAULTS.get(name, CATEGORY_DEFAULTS["unspecified-high"])


def list_categories() -> list[str]:
    """Return all category names in canonical order."""
    return list(CATEGORY_NAMES)


def category_table() -> str:
    """Render a markdown table describing every category (for prompts)."""
    lines = ["| Category | Reasoning | Use for |"]
    lines.append("|----------|-----------|---------|")
    for name in CATEGORY_NAMES:
        spec = CATEGORY_DEFAULTS[name]
        lines.append(f"| {name} | {spec.reasoning_intensity} | {spec.use_for} |")
    return "\n".join(lines)
