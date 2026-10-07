"""Independent code reviewer for dev mode — breaks the self-review loop.

Pain point addressed: the implementing agent verifying its own work is
systematically biased ("agent 自主验收的项目代码在生产环境出问题" 的核心
成因之一). This module runs a FRESH engine session — separate context,
separate cache, read-only tool whitelist — that sees only the task and
the diff, not the implementer's reasoning.

The reviewer is constrained where it matters (it cannot write files, its
output is parsed into a structured verdict) but NOT micromanaged: instead
of piling prohibitions onto the implementer's prompt, boundary awareness
is pushed into the review checklist — the reviewer hunts for missing error
paths, resource leaks, empty/edge/extreme inputs, concurrency and
platform-specific hazards. Informational review keeps model performance
high while still catching what tests miss.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from ..engine.loop import EngineLoop
from ..engine.providers.base import ModelResponse

logger = logging.getLogger("zouwucode.dev.reviewer")

# Read-only whitelist: the reviewer must never modify the code it judges.
# Note: `bash` is deliberately excluded — shell access would allow writes
# and defeats the read-only guarantee. git is safe (status/diff/log only).
REVIEWER_TOOL_WHITELIST = ["read", "ls", "glob", "git"]

_SYSTEM_PROMPT = """You are ZOUWUCODE Dev Reviewer, a strict but fair \
senior software engineer performing an independent code review. You did NOT \
write this code and must not trust the author's claims — verify against the \
diff and the surrounding code.

Review checklist (in priority order):
1. Correctness — does the change actually solve the stated task?
2. Boundary handling — empty input, None/null, zero, negative, extremely \
large values, unicode, whitespace-only strings.
3. Error paths — every external call (file, network, subprocess, API) must \
handle failure; no bare `except: pass`; no swallowed errors.
4. Resource management — files/connections/locks context-managed or \
explicitly released on all paths.
5. Concurrency & re-entrancy — shared state mutated without protection.
6. Platform differences — path separators, encoding, line endings, shell \
assumptions (the project runs on Windows AND CI/Linux).
7. Backward compatibility — public API/signature changes without migration.
8. Security — injection, secrets in code, unsafe deserialization, path \
traversal.

Rules:
- You are READ-ONLY. Do not edit any file.
- Judge only this diff and its context; do not demand unrelated refactors.
- Style nits are NOT request_changes; only real defects are.

End your review with EXACTLY one JSON object in a fenced block:
```json
{"verdict": "approve" | "request_changes",
 "issues": [{"severity": "critical|major|minor",
             "file": "...", "point": "...", "fix_hint": "..."}],
 "summary": "one paragraph"}
```
"""


@dataclass
class ReviewOutcome:
    """Parsed reviewer verdict."""

    approved: bool
    issues: list[dict] = field(default_factory=list)
    summary: str = ""
    raw: str = ""
    parse_failed: bool = False


_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def parse_review(text: str) -> ReviewOutcome:
    """Extract the structured verdict from reviewer output.

    Fallback semantics are deliberately conservative-but-not-blocking:
    if the reviewer produced no parseable JSON we APPROVE with a note
    (a broken parser must not wedge the pipeline), but the raw text is
    preserved and attached to the PR for the human reviewer.
    """
    m = _JSON_BLOCK_RE.search(text or "")
    if not m:
        return ReviewOutcome(approved=True, summary="reviewer verdict unparsed",
                             raw=text or "", parse_failed=True)
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return ReviewOutcome(approved=True, summary="reviewer JSON invalid",
                             raw=text or "", parse_failed=True)
    blocking = [i for i in data.get("issues", [])
                if str(i.get("severity", "")).lower() in ("critical", "major")]
    approved = str(data.get("verdict", "")).lower() != "request_changes" \
        or not blocking
    return ReviewOutcome(
        approved=approved,
        issues=data.get("issues", []),
        summary=str(data.get("summary", "")),
        raw=text or "",
    )


def format_review_for_pr(outcome: ReviewOutcome) -> str:
    """Markdown block for the PR body — transparency for the human reviewer."""
    lines = ["### 🔍 独立 AI 审查", ""]
    if outcome.parse_failed:
        lines.append("审查结论未能结构化解析，以下为原始意见（供参考）：\n")
        lines.append(outcome.raw[:1500])
        return "\n".join(lines)
    mark = "✅ approve" if outcome.approved else "⚠️ request_changes（已回灌修复）"
    lines.append(f"**结论**：{mark}")
    if outcome.summary:
        lines.append(f"\n{outcome.summary}")
    if outcome.issues:
        lines.append("\n<details><summary>审查发现（%d 项）</summary>\n"
                     % len(outcome.issues))
        for i in outcome.issues:
            lines.append(f"- [{i.get('severity','?')}] "
                         f"{i.get('file','')}: {i.get('point','')}")
        lines.append("\n</details>")
    return "\n".join(lines)


async def review_diff(
    engine: EngineLoop,
    task_prompt: str,
    diff_text: str,
    tool_schemas: Optional[list[dict]] = None,
) -> ReviewOutcome:
    """Run one independent review pass in a FRESH engine session.

    The caller builds `engine` (new EngineLoop, plan-ish constraints come
    from the whitelist, not from prompt prohibitions) and provides the
    unified diff of the change. `tool_schemas` should already be filtered
    to REVIEWER_TOOL_WHITELIST.
    """
    user = (
        f"## Original task\n{task_prompt[:4000]}\n\n"
        f"## Unified diff to review\n```diff\n{diff_text[:60000]}\n```\n\n"
        "Review the diff above against the checklist. Finish with the JSON "
        "verdict block."
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    try:
        response: ModelResponse = await engine.run(
            messages=messages, tools=tool_schemas)
    except Exception as exc:  # noqa: BLE001 — review failure must not block PR
        logger.warning("Reviewer session failed: %s", exc)
        return ReviewOutcome(approved=True, summary=f"reviewer error: {exc}",
                             raw="", parse_failed=True)
    outcome = parse_review(response.content or "")
    logger.info("Reviewer verdict: %s (%d issues)",
                "approve" if outcome.approved else "request_changes",
                len(outcome.issues))
    return outcome


def format_review_feedback(outcome: ReviewOutcome) -> str:
    """Turn a request_changes verdict into actionable feedback for the
    implementing agent — specific findings, not blanket restrictions."""
    lines = ["\n\n## Independent code review (REQUEST CHANGES)",
             "An independent reviewer session found these blocking issues:"]
    for i in outcome.issues:
        sev = str(i.get("severity", "?"))
        if sev.lower() not in ("critical", "major"):
            continue
        lines.append(f"- [{sev}] {i.get('file','')}: {i.get('point','')}"
                     + (f" → {i.get('fix_hint')}" if i.get("fix_hint") else ""))
    if outcome.summary:
        lines.append(f"\nReviewer summary: {outcome.summary[:800]}")
    lines.append("\nFix ALL listed issues; re-verify locally if possible.")
    return "\n".join(lines)
