"""Tests for the Web UI — welcome-screen font compression regression.

Verifies the ASCII-art banner keeps its 6-line structure (white-space: pre)
so it never collapses into the vertically-compressed 2-line mush.
"""

import re

from zouwucode.webui.server import HTML_TEMPLATE


def _art_lines() -> list[str]:
    """Extract the lines inside `<div class="welcome-art">...</div>`."""
    m = re.search(r'<div class="welcome-art">(.*?)</div>', HTML_TEMPLATE, re.S)
    assert m, "welcome-art block not found in HTML"
    return m.group(1).split("\n")


class TestWelcomeArt:
    def test_white_space_pre_present(self):
        """CSS rule must keep the art newlines (white-space: pre)."""
        m = re.search(r"#welcome \.welcome-art \{.*?\}", HTML_TEMPLATE, re.S)
        assert m, "welcome-art CSS rule not found"
        assert "white-space: pre" in m.group(0), (
            "缺少 white-space: pre — ASCII 艺术字换行符会被折叠成空格，"
            "导致横幅纵向压缩变形"
        )

    def test_art_renders_exactly_six_lines(self):
        """Banner must render 6 non-empty lines with no leading/trailing blank."""
        lines = _art_lines()
        non_empty = [l for l in lines if l.strip()]
        assert len(non_empty) == 6, f"艺术字应为 6 行，实际 {len(non_empty)} 行"
        # 首行与末行之间不能有空白行残留
        assert lines[0].strip(), "横幅首行为空（前导换行符残留）"
        assert lines[-1].strip(), "横幅末行为空（尾部空格/换行残留）"
        assert not any(not l.strip() for l in lines), "横幅中间存在空行"

    def test_art_letters_complete(self):
        """Block letters for ZOUWUCODE must all be present."""
        lines = _art_lines()
        assert "███████╗" in lines[0], "首行 Z 缺失"
        assert "╚══════╝" in lines[-1], "末行 E 缺失"

    def test_mobile_safe_overflow_rule(self):
        """Narrow screens must not stretch the layout."""
        m = re.search(r"#welcome \.welcome-art \{.*?\}", HTML_TEMPLATE, re.S)
        rule = m.group(0) if m else ""
        assert "overflow-x: auto" in rule, "窄屏下横幅应可横向滚动而不是撑破布局"
        assert "max-width: 100%" in rule, "横幅应受容器宽度约束"


class TestResponsiveLayout:
    """Regression tests for narrow-screen header + long-token wrapping fixes."""

    @staticmethod
    def _css() -> str:
        m = re.search(r"<style>(.*?)</style>", HTML_TEMPLATE, re.S)
        assert m, "CSS block not found"
        return m.group(1)

    def test_header_wraps_on_narrow_screens(self):
        """Header must not clip on <560px screens (flex-wrap media query)."""
        css = self._css()
        m = re.search(r"@media \(max-width: 560px\) \{.*?\}", css, re.S)
        assert m, "缺少窄屏断点 @media (max-width: 560px)"
        block = m.group(0)
        assert "#header" in block
        assert "flex-wrap: wrap" in block, "窄屏下 header 应允许换行而非横向溢出被裁剪"
        assert "height: auto" in block, "换行后 header 高度应自适应"

    def test_rules_content_wraps_long_tokens(self):
        """rules-content long URLs/code tokens must wrap, not scroll sideways."""
        m = re.search(r"#sidebar \.rules-content \{.*?\}", self._css(), re.S)
        assert m, "rules-content rule not found"
        rule = m.group(0)
        assert "overflow-wrap: anywhere" in rule, "长 token 应折行而不是横向溢出"
        assert "overflow-x: hidden" in rule, "不应出现内部横向滚动条"

    def test_session_item_wraps_long_names(self):
        """session-item long names must wrap."""
        m = re.search(r"#sidebar \.session-item \{.*?\}", self._css(), re.S)
        assert m, "session-item rule not found"
        assert "overflow-wrap: anywhere" in m.group(0), "会话长名称应折行"

    def test_thinking_body_scrolls(self):
        """thinking-body must cap height and scroll internally."""
        m = re.search(r"\.msg\.thinking \.thinking-body \{.*?\}", self._css(), re.S)
        assert m, "thinking-body rule not found"
        rule = m.group(0)
        assert "max-height: 240px" in rule, "思考块应限制最大高度"
        assert "overflow-y: auto" in rule, "超出高度应内部滚动"


class TestStreamingThinkingWeb:
    """Streaming thinking display — SSE endpoint + /thinking toggle wiring."""

    def test_frontend_uses_streaming_endpoint(self):
        """Main chat flow must consume /api/chat/stream (SSE), not plain JSON."""
        assert "/api/chat/stream" in HTML_TEMPLATE, "前端应调用 SSE 流式端点 /api/chat/stream"
        assert "resp.body.getReader()" in HTML_TEMPLATE, "前端应通过 ReadableStream 读取 SSE"

    def test_streaming_endpoint_in_server(self):
        """Server must route /api/chat/stream to the SSE handler."""
        import inspect
        from zouwucode.webui import server
        src = inspect.getsource(server)
        assert 'path == "/api/chat/stream"' in src, "服务端应路由 /api/chat/stream"
        assert "_handle_chat_stream" in src, "应实现 SSE 流式聊天处理器"
        assert "text/event-stream" in src, "SSE 响应头应为 text/event-stream"
        assert '"type": "thinking"' in src, "SSE 应推送 thinking 增量事件"

    def test_thinking_toggle_api(self):
        """Server must expose /api/thinking toggle endpoint."""
        import inspect
        from zouwucode.webui import server
        src = inspect.getsource(server)
        assert 'path == "/api/thinking"' in src, "应提供 /api/thinking 切换端点"
        assert "show_thinking" in src, "/api/thinking 应切换 show_thinking 配置"

    def test_frontend_thinking_command(self):
        """Frontend must handle the /thinking command."""
        assert "toggleThinking()" in HTML_TEMPLATE, "前端应实现 toggleThinking()"
        assert "/thinking" in HTML_TEMPLATE, "前端应支持 /thinking 命令"

    def test_status_bar_shows_thinking(self):
        """Status bar must display the thinking toggle state."""
        assert 'id="status-thinking"' in HTML_TEMPLATE, "状态栏应显示 Thinking 状态"

    def test_add_thinking_stream_helper(self):
        """Frontend must have a live streaming thinking block helper."""
        assert "function addThinkingStream()" in HTML_TEMPLATE, "应实现 addThinkingStream() 实时思考块"


class _SSEWriter:
    def __init__(self):
        self.chunks = []

    def write(self, data: bytes):
        self.chunks.append(data)

    async def drain(self):
        pass

    def close(self):
        pass


class _FakeStats:
    hit_rate = 0.5
    total_cost = 0.01


class _FakeEngine:
    on_thinking_delta = None
    on_tool_event = None
    stats = _FakeStats()

    async def run(self, messages, tools=None):
        # Simulate the engine streaming thinking deltas via the hook
        if self.on_thinking_delta is not None:
            self.on_thinking_delta("let me ")
            self.on_thinking_delta("analyze")
        from zouwucode.engine.providers.base import ModelResponse
        return ModelResponse(content="Done", thinking="let me analyze",
                             cache_hit=True, usage={})


class _FakeTools:
    def get_schemas(self):
        return []


class _FakeCoordinator:
    project_memory = None


class TestSSEHandlerProtocol:
    """Functional test of the SSE streaming chat handler protocol."""

    def _make_server(self, show_thinking=True):
        from zouwucode.webui.server import WebUIServer
        from zouwucode.config import ZOUWUCODEConfig
        config = ZOUWUCODEConfig()
        config.show_thinking = show_thinking
        engine = _FakeEngine()
        srv = WebUIServer(engine=engine, tools=_FakeTools(),
                          coordinator=_FakeCoordinator(), config=config,
                          modules=None)
        return srv, engine

    def test_sse_emits_thinking_then_done(self):
        import asyncio
        import json as _json
        srv, engine = self._make_server(show_thinking=True)
        writer = _SSEWriter()

        asyncio.run(srv._handle_chat_stream(writer, '{"message": "hi"}'))

        text = b"".join(writer.chunks).decode("utf-8")
        assert "text/event-stream" in text, "应为 SSE 响应"
        events = [l[6:] for l in text.splitlines() if l.startswith("data: ")]
        evts = [_json.loads(e) for e in events]
        types = [e["type"] for e in evts]
        assert types == ["thinking", "thinking", "done"], types
        assert [e["delta"] for e in evts[:2]] == ["let me ", "analyze"]
        assert evts[-1]["content"] == "Done"
        assert evts[-1]["cache_hit"] is True
        assert engine.on_thinking_delta is None, "结束后应解绑流式回调"

    def test_sse_suppresses_thinking_when_disabled(self):
        import asyncio
        import json as _json
        srv, _ = self._make_server(show_thinking=False)
        writer = _SSEWriter()

        asyncio.run(srv._handle_chat_stream(writer, '{"message": "hi"}'))

        text = b"".join(writer.chunks).decode("utf-8")
        events = [l[6:] for l in text.splitlines() if l.startswith("data: ")]
        evts = [_json.loads(e) for e in events]
        assert all(e["type"] != "thinking" for e in evts), "关闭后不应推送 thinking 事件"
