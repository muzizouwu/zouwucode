"""Shared token estimation — one estimator everywhere.

The old rule (``len // 2``) UNDER-estimated English by ~2x, so compression
fired too late, the context-budget warning was wrong, and a big prompt could
silently blow past the provider limit (400). CJK is ~1 token/char; latin is
~4 chars/token, so estimate the two scripts separately.
"""

import re

_CJK = re.compile(r"[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef]")


def estimate_tokens(text) -> int:
    """Approximate token count for a single string."""
    if not text:
        return 0
    s = str(text)
    cjk = len(_CJK.findall(s))
    other = len(s) - cjk
    return cjk + (other + 3) // 4


def estimate_messages(messages, overhead: int = 4) -> int:
    """Approximate token count for a message list (content + tool_calls)."""
    total = 0
    for m in messages or []:
        total += estimate_tokens(m.get("content") or "")
        calls = m.get("tool_calls")
        if calls:
            total += estimate_tokens(str(calls))
        total += overhead
    return total
