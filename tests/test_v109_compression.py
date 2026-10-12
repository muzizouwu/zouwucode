"""Regression for v1.0.9: compression correctness + one token estimator + TUI body streaming.

Pins the audit's engine/context defects:
- D13: token estimation was len//2 (≈2x under for English) in four places;
- D10: the compressor dropped empty-content assistant messages — exactly the
  tool_calls carriers — orphaning their tool results;
- D9: aggressive compression injected a mid-conversation "system" message;
- D11: file-content compression replaced a whole file with "N lines (compressed)";
- D12: project memory was injected as a mid-conversation "system" message;
- TUI only streamed thinking, not the assistant body.
"""

from pathlib import Path

from zouwucode.context.compressor import DialogueCompressor
from zouwucode.engine.cache import PrefixCache
from zouwucode.tokens import estimate_messages, estimate_tokens

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestTokenEstimator:
    def test_english_not_halved(self):
        text = "a" * 400  # 400 ASCII chars ≈ 100 tokens, not 200
        assert 90 <= estimate_tokens(text) <= 110

    def test_cjk_near_one_per_char(self):
        text = "中" * 100
        assert estimate_tokens(text) >= 100

    def test_mixed_and_empty(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens("hello 世界") > 0

    def test_counts_tool_calls(self):
        msgs = [{"role": "assistant", "content": "",
                 "tool_calls": [{"function": {"arguments": "x" * 200}}]}]
        assert estimate_messages(msgs) > 40

    def test_wired_into_cache_and_loop(self):
        cache = PrefixCache(max_prefix_tokens=10**9)
        cache.freeze([{"role": "user", "content": "a" * 400}])
        # 400 ASCII chars ≈ 100 tokens (old code said 200)
        assert 90 <= cache.get_prefix_length() <= 110
        assert "estimate_messages(messages)" in _read("zouwucode/engine/loop.py")
        assert "estimate_tokens" in _read("zouwucode/context/sliding_window.py")


class TestCompressorKeepsToolCalls:
    def _msgs(self):
        return [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "result body"},
        ]

    def test_balanced_keeps_tool_call_carrier(self):
        out = DialogueCompressor(level="balanced")._balanced_compress(self._msgs())
        assert any(m.get("tool_calls") for m in out)

    def test_lossless_keeps_tool_call_carrier(self):
        out = DialogueCompressor(level="lossless")._lossless_compress(self._msgs())
        assert any(m.get("tool_calls") for m in out)


class TestCompressorNoMidSystem:
    def test_aggressive_never_injects_system(self):
        messages = []
        for i in range(12):
            messages.append({"role": "user", "content": f"q{i}"})
            messages.append({"role": "assistant", "content": f"a{i}"})
        out = DialogueCompressor(level="aggressive")._aggressive_compress(messages)
        assert not any(m["role"] == "system" for m in out)
        # summaries are still present, as user notes
        assert any("[Compressed" in m["content"] for m in out)


class TestFileContentSummary:
    def test_keeps_head_and_tail(self):
        lines = [f"{i}→code line {i}" for i in range(1, 41)]
        content = "\n".join(lines)
        out = DialogueCompressor()._summarize_tool_result(content)
        assert "[File Content]" in out and "more lines" in out
        assert "code line 1" in out      # head kept
        assert "code line 40" in out     # tail kept


class TestNoMidConversationSystem:
    def test_project_memory_uses_user_role(self):
        for rel in ("zouwucode/tui/app.py", "zouwucode/tui/textual_app.py"):
            src = _read(rel)
            assert '"role": "user", "content": "[project memory]' in src
            assert '"role": "system", "content": project_c' not in src


class TestTuiBodyStreaming:
    src = None

    @classmethod
    def setup_class(cls):
        cls.src = _read("zouwucode/tui/textual_app.py")

    def test_streaming_methods_present(self):
        assert "def _on_content_delta" in self.src
        assert "def _flush_content_line" in self.src

    def test_hook_wired_and_cleared(self):
        assert "self.engine.on_content_delta = self._on_content_delta" in self.src
        assert "self.engine.on_content_delta = None" in self.src

    def test_no_double_render(self):
        assert "if response.content and not self._content_started:" in self.src
