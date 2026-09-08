"""IntentGate — classifies what the user actually wants (ported from oh-my-opencode).

Before acting on any request, the main orchestrator (Sisyphus) classifies
the user's *true intent* — research, implementation, investigation, fix,
planning, etc. — and routes accordingly. This avoids always running the
heaviest pipeline for every request.

The classifier is deliberately lightweight: a rule-based scorer first,
with an optional LLM-backed refinement when available. The rule-based
scorer is deterministic and fast, so tests and offline use are stable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Intent(str, Enum):
    """Canonical intents understood by the orchestrator."""

    RESEARCH = "research"            # 查证、搜索、理解现有代码
    EXPLORE = "explore"              # 快速定位符号/文件/模式
    INVESTIGATION = "investigation"  # 排查问题、定位 bug 根因
    FIX = "fix"                      # 修复 bug
    IMPLEMENTATION = "implementation"  # 实现新功能、写代码
    REFACTOR = "refactor"            # 重构、清理
    PLANNING = "planning"            # 制定方案/计划
    ARCHITECTURE = "architecture"    # 架构设计、技术选型
    REVIEW = "review"                # 审查代码/方案
    TEST = "test"                    # 编写/运行测试
    DOCUMENTATION = "documentation"  # 文档
    QUICK_QUESTION = "quick_question"  # 简单问答
    UNKNOWN = "unknown"


# ── Keyword rules ─────────────────────────────────────────────────────────────
# Intent → weighted keywords. Ported from oh-my-opencode's IntentGate
# philosophy; weights tuned for Chinese + English mixed input.
KEYWORD_RULES: dict[Intent, list[tuple[str, int]]] = {
    Intent.EXPLORE: [
        (r"grep", 3), (r"search", 2), (r"find", 2), (r"locate", 2),
        (r"where is", 2), (r"where's", 2), (r"查找", 2), (r"定位", 2),
        (r"搜索", 2), (r"在哪里", 2), (r"which file", 3), (r"符号", 2),
        (r"symbol", 2), (r"definition", 2), (r"快速.*找", 2),
    ],
    Intent.RESEARCH: [
        (r"research", 3), (r"investigate how", 3), (r"how does", 2),
        (r"how is", 2), (r"what does", 2), (r"understand", 2),
        (r"explain", 2), (r"调研", 3), (r"研究", 3), (r"了解", 2),
        (r"是什么", 2), (r"怎么回事", 3), (r"原理", 2), (r"docs?", 2),
        (r"文档", 2), (r"documentation", 2), (r"api 用法", 2),
    ],
    Intent.INVESTIGATION: [
        (r"why.*(fail|error|crash|bug)", 3), (r"debug", 3),
        (r"root cause", 3), (r"排查", 3), (r"为什么.*(报错|失败|崩溃)", 3),
        (r"原因", 2), (r"issue", 2), (r"problem", 2), (r"trace", 2),
        (r"堆栈", 2), (r"traceback", 3), (r"stuck", 2), (r"卡住", 2),
    ],
    Intent.FIX: [
        (r"fix", 3), (r"bug", 3), (r"broken", 3), (r"修复", 3),
        (r"错误", 2), (r"修一下", 3), (r"repair", 3), (r"解决.*(问题|报错)", 3),
        (r"not working", 3), (r"坏了", 3), (r"崩溃", 2), (r"error:", 2),
    ],
    Intent.IMPLEMENTATION: [
        (r"implement", 3), (r"add", 2), (r"create", 2), (r"build", 2),
        (r"write", 2), (r"实现", 3), (r"新增", 2), (r"开发", 3), (r"编写", 2),
        (r"写一个", 3), (r"做一个", 3), (r"功能", 2), (r"feature", 2),
        (r"模块", 2), (r"from scratch", 2), (r"全新", 2),
    ],
    Intent.REFACTOR: [
        (r"refactor", 3), (r"重构", 3), (r"clean up", 2), (r"整理", 2),
        (r"优化.*(结构|代码)", 3), (r"simplify", 2), (r"简化", 2),
        (r"rename", 2), (r"重命名", 2), (r"拆分", 2), (r"extract", 2),
    ],
    Intent.PLANNING: [
        (r"plan", 3), (r"方案", 3), (r"计划", 3), (r"step-by-step", 3),
        (r"步骤", 2), (r"outline", 2), (r"规划", 3), (r"roadmap", 2),
        (r"思路", 2), (r"怎么做", 2), (r"approach", 2), (r"策略", 2),
    ],
    Intent.ARCHITECTURE: [
        (r"architecture", 3), (r"架构", 3), (r"design", 2), (r"设计", 2),
        (r"tech.?stack", 2), (r"技术选型", 3), (r"trade-?off", 2),
        (r"权衡", 2), (r"system design", 3), (r"数据库.*选", 2),
        (r"schema", 2), (r"microservice", 2), (r"微服务", 2),
    ],
    Intent.REVIEW: [
        (r"review", 3), (r"审查", 3), (r"code review", 3), (r"评审", 3),
        (r"check my code", 3), (r"帮我看看", 2), (r"检查.*代码", 2),
        (r"audit", 2), (r"安全", 2), (r"security", 2),
    ],
    Intent.TEST: [
        (r"test", 2), (r"测试", 2), (r"unit test", 3), (r"pytest", 3),
        (r"写测试", 3), (r"跑测试", 3), (r"coverage", 2), (r"覆盖率", 2),
    ],
    Intent.DOCUMENTATION: [
        (r"document", 2), (r"docstring", 2), (r"文档", 2), (r"注释", 2),
        (r"readme", 3), (r"comment", 2), (r"写说明", 2), (r"usage", 2),
    ],
    Intent.QUICK_QUESTION: [
        (r"what is", 1), (r"什么意思", 1), (r"？", 1), (r"\?", 1),
        (r"解释", 1), (r"tell me", 1), (r"简单说", 1),
    ],
}

# Short messages (< N chars) that are questions default to quick_question.
_QUICK_QUESTION_LEN = 40


@dataclass
class IntentResult:
    """Result of intent classification."""

    intent: Intent
    confidence: float
    scores: dict[str, float] = field(default_factory=dict)
    matched_keywords: list[str] = field(default_factory=list)


class IntentGate:
    """Classifies user requests into intents."""

    # Rules that always win (high confidence, unambiguous)
    _HARD_RULES: dict[Intent, list[str]] = {
        Intent.EXPLORE: [r"grep", r"which file", r"符号.*定义", r"find .*symbol"],
        Intent.FIX: [r"\bfix\b", r"\bbug\b", r"修复"],
        Intent.IMPLEMENTATION: [r"\bimplement\b", r"实现", r"写一个", r"开发"],
        Intent.PLANNING: [r"\bplan\b", r"方案", r"计划", r"规划"],
        Intent.ARCHITECTURE: [r"架构", r"architecture", r"技术选型"],
        Intent.TEST: [r"pytest", r"unit test", r"写测试"],
    }

    def __init__(self, use_llm: bool = False, llm_classifier=None):
        """Create an intent gate.

        Args:
            use_llm: whether to attempt LLM-backed refinement (optional).
            llm_classifier: optional async callable(request: str) -> Intent;
                used only when rule-based confidence is low.
        """
        self._use_llm = use_llm
        self._llm_classifier = llm_classifier

    def classify(self, request: str) -> IntentResult:
        """Classify a user request into an intent."""
        text = request.strip()
        text_lower = text.lower()

        scores: dict[str, float] = {}
        matched: list[str] = []

        for intent, rules in KEYWORD_RULES.items():
            total = 0.0
            for pattern, weight in rules:
                if re.search(pattern, text_lower):
                    total += weight
                    matched.append(pattern)
            if total > 0:
                scores[intent.value] = total

        # Hard rules override everything (unambiguous keywords)
        for intent, patterns in self._HARD_RULES.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    scores[intent.value] = max(scores.get(intent.value, 0), 10.0)

        # Short pure questions default to quick_question
        if not scores and len(text) <= _QUICK_QUESTION_LEN and text.endswith(("?", "？", "吗")):
            scores[Intent.QUICK_QUESTION.value] = 2.0

        if not scores:
            scores[Intent.UNKNOWN.value] = 0.1

        best_name = max(scores, key=scores.get)
        best_score = scores[best_name]
        total = sum(scores.values()) or 1.0
        confidence = best_score / total

        result = IntentResult(
            intent=Intent(best_name),
            confidence=confidence,
            scores=scores,
            matched_keywords=matched,
        )

        return result

    async def classify_async(self, request: str) -> IntentResult:
        """Async variant — used when an async LLM classifier is wired in."""
        result = self.classify(request)
        if (
            self._use_llm
            and self._llm_classifier is not None
            and result.confidence < 0.5
        ):
            try:
                refined = await self._llm_classifier(request)
                if refined is not None:
                    result.intent = refined
                    result.confidence = 0.8
            except Exception:
                pass  # fall back to rule-based result
        return result


def intent_label(intent: Intent) -> str:
    """Human-readable Chinese label for an intent."""
    labels = {
        Intent.RESEARCH: "调研/理解",
        Intent.EXPLORE: "快速定位",
        Intent.INVESTIGATION: "问题排查",
        Intent.FIX: "修复 Bug",
        Intent.IMPLEMENTATION: "实现功能",
        Intent.REFACTOR: "重构",
        Intent.PLANNING: "规划方案",
        Intent.ARCHITECTURE: "架构设计",
        Intent.REVIEW: "代码审查",
        Intent.TEST: "测试",
        Intent.DOCUMENTATION: "文档",
        Intent.QUICK_QUESTION: "快速问答",
        Intent.UNKNOWN: "未分类",
    }
    return labels.get(intent, intent.value)


def intent_routing_prompt() -> str:
    """Render the IntentGate routing table for the Sisyphus prompt."""
    rows = [
        "| Intent | Route to |",
        "|--------|----------|",
        "| research / explore | Librarian / Explore agent |",
        "| investigation | Explore + Oracle (root cause) |",
        "| fix | Atlas → Sisyphus-Junior (category=deep) |",
        "| implementation | Atlas → Sisyphus-Junior (category=deep) |",
        "| refactor | Atlas → Sisyphus-Junior (category=ultrabrain) |",
        "| planning | Prometheus agent |",
        "| architecture | Oracle agent |",
        "| review | Oracle / Momus |",
        "| test | Sisyphus-Junior (category=quick) |",
        "| documentation | Sisyphus-Junior (category=writing) |",
        "| quick_question | answer directly (category=quick) |",
    ]
    return "\n".join(rows)
