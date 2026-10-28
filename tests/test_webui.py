"""Tests for the GUI frontend (static assets) + SSE protocol.

The GUI moved out of a 1500-line string inside server.py into
``zouwucode/webui/static`` (HTML + CSS + JS). These tests pin the contract the
JavaScript depends on, so a redesign cannot silently break serving, the DOM, or
the REST surface — plus the existing functional SSE-protocol tests.
"""

import re

from zouwucode.webui import server

INDEX = server.read_static("index.html")[0]
CSS = server.read_static("style.css")[0]
JS = server.read_static("app.js")[0]


def _ids(markup: str) -> set:
    return set(re.findall(r'id="([^"]+)"', markup))


class TestAssets:
    def test_all_three_assets_ship(self):
        assert len(INDEX) > 1500 and "<!DOCTYPE html>" in INDEX
        assert len(CSS) > 3000
        assert len(JS) > 3000

    def test_content_types(self):
        assert server.read_static("index.html")[1].startswith("text/html")
        assert server.read_static("style.css")[1].startswith("text/css")
        assert server.read_static("app.js")[1].startswith("application/javascript")

    def test_no_external_cdn(self):
        """The GUI must work offline: no http(s) asset URLs in markup."""
        assert not re.search(r'(src|href)="https?://', INDEX), "GUI 不应依赖外部 CDN"
        assert "<script src=\"/static/app.js\"" in INDEX
        assert 'href="/static/style.css"' in INDEX

    def test_traversal_and_dotfiles_rejected(self):
        assert server.read_static("../server.py") is None
        assert server.read_static(".hidden") is None
        assert server.read_static("nested/app.js") is None
        assert server.read_static("") is None


class TestDomContract:
    def test_core_landmarks_exist(self):
        ids = _ids(INDEX)
        for required in ("chat", "input", "send-btn", "interrupt-btn",
                         "composer", "approval", "interrupt-confirm",
                         "palette", "session-list", "status-mode",
                         "status-cache", "status-cost", "toasts"):
            assert required in ids, f"缺少 DOM 节点 #{required}"

    def test_every_js_selector_has_a_node(self):
        """Catch typos: each $("#id") in app.js must exist in index.html."""
        ids = _ids(INDEX)
        wanted = set(re.findall(r'\$\("#([\w-]+)"\)', JS))
        missing = sorted(wanted - ids)
        assert not missing, f"app.js 引用了不存在的节点: {missing}"


class TestApiContract:
    def test_every_api_call_is_routed(self):
        import inspect
        from zouwucode.webui.server import WebUIServer

        src = inspect.getsource(WebUIServer)
        called = set(re.findall(r'"/api/([\w/-]+)"', JS))
        assert called, "前端应调用 API"
        missing = sorted(f"/api/{name}" for name in called if f'"/api/{name}"' not in src)
        assert not missing, f"前端调用了服务端未注册的路由: {missing}"

    def test_streaming_and_approval_protocol(self):
        assert "/api/chat/stream" in JS
        assert "getReader()" in JS
        assert "/api/approve" in JS and "/api/interrupt" in JS
        for evt in ("thinking", "content", "approval_request", "done",
                    "interrupted", "error"):
            assert evt in JS, f"前端应处理 SSE 事件 {evt}"


class TestUxBehaviour:
    def test_interrupt_requires_confirmation(self):
        assert 'id="interrupt-confirm"' in INDEX, "打断必须二次确认（防误触）"
        assert 'id="interrupt-ok"' in INDEX and 'id="interrupt-cancel"' in INDEX
        assert "Escape" in JS and "requestInterrupt" in JS

    def test_esc_is_the_only_interrupt_key(self):
        low = JS.lower()
        assert "ctrl+x" not in low, "Ctrl+X 已废弃"
        assert "Escape" in JS

    def test_composer_shortcuts(self):
        assert "shiftKey" in JS, "Enter 发送 / Shift+Enter 换行"
        assert "autoGrow" in JS

    def test_theme_and_responsive(self):
        assert "localStorage" in JS, "主题选择应持久化"
        assert "dataset.theme" in JS, "应把主题写到 <html data-theme=…>"
        assert "@media (max-width: 1180px)" in CSS
        assert "@media (max-width: 860px)" in CSS
        assert "overflow-wrap: anywhere" in CSS, "长 token 应折行"
        assert "prefers-reduced-motion" in CSS

    def test_thinking_is_collapsible(self):
        assert "details" in JS and "thinking" in JS
        assert "details.thinking" in CSS

    def test_code_blocks_have_copy(self):
        assert "code-block" in JS and "clipboard" in JS
        assert ".code-block" in CSS


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
