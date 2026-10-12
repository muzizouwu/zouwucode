"""Regression: opt-in engine-side context compression + cache budget.

Defects pinned:
- DialogueCompressor existed but the ENGINE never used it anywhere
  (only the TUI instantiated it, for stats); CLI flags --compress /
  --max-tokens / --keep-turns were display-only.
- Compression must be OFF by default (a byte-stable prefix is the whole
  premise of the server-side prompt cache) and must never break the
  tool-call/result pairing providers require.
- PrefixCache.can_append() had zero production callers: the documented
  max_prefix_tokens was never checked. Now it warns once per instance.
"""

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.engine.cache import PrefixCache
from zouwucode.engine.loop import EngineLoop


class _StubProvider:
    """_prepare_messages never touches the provider; fail loudly if it does."""

    async def complete(self, **kwargs):
        raise AssertionError("provider must not be called in these tests")


def _engine(enabled=False, **ctx_kw):
    cfg = ZOUWUCODEConfig()
    cfg.context.enabled = enabled
    for key, value in ctx_kw.items():
        setattr(cfg.context, key, value)
    return EngineLoop(cfg, _StubProvider())


def _text_history():
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(30):
        msgs.append({"role": "user", "content": "Q%d " % i + "x" * 400})
        msgs.append({"role": "assistant", "content": "A%d " % i + "y" * 400})
    return msgs


def _tool_history():
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(12):
        msgs.append({"role": "user", "content": "ask %d " % i + "z" * 300})
        msgs.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": f"c{i}", "type": "function",
                            "function": {"name": "read", "arguments": "{}"}}],
        })
        msgs.append({"role": "tool", "tool_call_id": f"c{i}",
                     "content": "result %d " % i + "w" * 300})
    return msgs


def _assert_pairing(messages):
    """API contract: tool results follow their requesting call, and
    every retained tool_calls set is fully answered before the next
    non-tool message."""
    pending = []
    for msg in messages:
        role = msg.get("role")
        if role == "assistant":
            assert not pending, ("unanswered tool_calls", pending)
            pending = [c.get("id") for c in (msg.get("tool_calls") or [])]
        elif role == "tool":
            assert msg.get("tool_call_id") in pending, "orphan tool result"
            pending.remove(msg.get("tool_call_id"))
        else:
            assert not pending, ("unanswered tool_calls", pending)
    assert not pending, ("unanswered tool_calls at end", pending)


class TestCompressionOff:
    def test_disabled_returns_identity_even_when_oversized(self):
        eng = _engine(enabled=False, max_context_tokens=10)
        msgs = _text_history()
        assert eng._prepare_messages(msgs) is msgs

    def test_default_config_is_off(self):
        cfg = ZOUWUCODEConfig()
        assert cfg.context.enabled is False
        assert cfg.context.keep_last_n == 50


class TestCompressionOn:
    def test_small_history_untouched(self):
        eng = _engine(enabled=True, max_context_tokens=10_000_000)
        msgs = _text_history()
        assert eng._prepare_messages(msgs) is msgs

    def test_aggressive_shrinks_and_keeps_tail(self):
        eng = _engine(enabled=True, level="aggressive",
                      max_context_tokens=1000, keep_last_n=10)
        msgs = _text_history()
        out = eng._prepare_messages(msgs)
        assert out is not msgs
        assert len(out) < len(msgs)
        assert out[-10:] == msgs[-10:]  # newest messages pass verbatim

    def test_balanced_never_breaks_pairing(self):
        eng = _engine(enabled=True, level="balanced",
                      max_context_tokens=1000, keep_last_n=4)
        out = eng._prepare_messages(_tool_history())
        _assert_pairing(out)
        assert out[-4:] == _tool_history()[-4:]

    def test_aggressive_never_breaks_pairing(self):
        eng = _engine(enabled=True, level="aggressive",
                      max_context_tokens=100, keep_last_n=2)
        out = eng._prepare_messages(_tool_history())
        _assert_pairing(out)

    def test_repair_strips_unanswered_tool_calls(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "text kept",
             "tool_calls": [{"id": "x1", "type": "function",
                             "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "user", "content": "next"},
        ]
        repaired = EngineLoop._repair_tool_pairing(msgs)
        assert repaired[1].get("tool_calls") is None
        assert repaired[1]["content"] == "text kept"

    def test_repair_drops_orphan_tool_results(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "tool", "tool_call_id": "gone", "content": "orphan"},
            {"role": "user", "content": "next"},
        ]
        repaired = EngineLoop._repair_tool_pairing(msgs)
        assert all(m.get("role") != "tool" for m in repaired)


class TestLateEnable:
    """CLI applies --compress AFTER the app (and engine) is constructed —
    the compressor must follow the current config, not only what was set
    when the engine was built, or the flags are silently ignored."""

    def test_enabling_after_construction_takes_effect(self):
        eng = _engine(enabled=False)
        msgs = _text_history()
        assert eng._prepare_messages(msgs) is msgs  # off at construction
        eng.config.context.enabled = True
        eng.config.context.level = "aggressive"
        eng.config.context.max_context_tokens = 1000
        eng.config.context.keep_last_n = 10
        out = eng._prepare_messages(msgs)
        assert out is not msgs and len(out) < len(msgs)

    def test_disabling_after_construction_stops_compressing(self):
        eng = _engine(enabled=True, level="aggressive",
                      max_context_tokens=1000, keep_last_n=10)
        eng.config.context.enabled = False
        msgs = _text_history()
        assert eng._prepare_messages(msgs) is msgs

    def test_level_change_rebuilds_the_compressor(self):
        eng = _engine(enabled=True, level="balanced",
                      max_context_tokens=1000, keep_last_n=10)
        eng._prepare_messages(_text_history())
        first = eng._context_compressor
        eng.config.context.level = "aggressive"
        eng._prepare_messages(_text_history())
        assert eng._context_compressor is not first
        assert eng._context_compressor.level == "aggressive"


class TestCompressorTermination:
    """Pinned defect: an unreachable token budget made
    compress_conversation recurse with identical arguments until
    RecursionError (min(0,5)==0 / min(5,5)==5)."""

    def test_tiny_budget_terminates(self):
        from zouwucode.context.compressor import DialogueCompressor
        msgs = _text_history()
        for level in ("aggressive", "balanced"):
            comp = DialogueCompressor(level=level)
            out = comp.compress_conversation(
                msgs, keep_last_n=0, max_context_tokens=10)
            assert isinstance(out, list) and out

    def test_normal_budget_terminates_with_default_keep(self):
        from zouwucode.context.compressor import DialogueCompressor
        comp = DialogueCompressor(level="aggressive")
        out = comp.compress_conversation(
            _text_history(), keep_last_n=50, max_context_tokens=10)
        assert isinstance(out, list) and out


class TestPrefixCacheBudget:
    def test_over_budget_warning_fires_once_per_instance(self, caplog):
        cache = PrefixCache(max_prefix_tokens=10)
        with caplog.at_level("WARNING"):
            cache.append({"role": "user", "content": "x" * 100})
            cache.append({"role": "user", "content": "y" * 100})
        hits = [r for r in caplog.records if "over budget" in r.getMessage()]
        assert len(hits) == 1
        # data is kept — append-only contract is not violated
        assert len(cache.get_prefix()) == 2
