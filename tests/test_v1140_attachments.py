"""Attachment intake (v1.14.0) — uploads become workspace files.

The contract pinned here: uploads are written under ``.zouwucode/uploads/`` so
the agent can read them with its tools, the prompt names every path, and only
vision-capable providers get the image inlined. Both chat endpoints must honour
the same rules.
"""

import asyncio
import base64
import json
import re

from zouwucode.attachments import (
    MAX_BYTES,
    MAX_FILES,
    attachment_note,
    filename_for,
    image_parts,
    save_attachments,
)
from zouwucode.webui import server as web_server

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
INDEX = web_server.read_static("index.html")[0]
JS = web_server.read_static("app.js")[0]
CSS = web_server.read_static("style.css")[0]


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


class TestSaveAttachments:
    def test_writes_into_workspace_uploads(self, tmp_path):
        saved, errors = save_attachments(
            [{"name": "shot.png", "mime": "image/png", "data": _b64(PNG)}],
            tmp_path,
        )
        assert errors == []
        assert len(saved) == 1
        entry = saved[0]
        assert entry["name"] == "shot.png"
        assert entry["mime"] == "image/png"
        assert entry["size"] == len(PNG)
        assert entry["relative"].startswith(".zouwucode/uploads/")
        assert (tmp_path / entry["relative"]).read_bytes() == PNG

    def test_accepts_data_url_prefix(self, tmp_path):
        data = "data:image/png;base64," + _b64(PNG)
        saved, errors = save_attachments(
            [{"name": "a.png", "mime": "image/png", "data": data}], tmp_path)
        assert errors == [] and saved[0]["size"] == len(PNG)

    def test_filename_is_sanitized_and_unique(self):
        first = filename_for("../../evil name.png", 1)
        second = filename_for("../../evil name.png", 2)
        assert "/" not in first and "\\" not in first and ".." not in first
        assert first.endswith(".png")
        assert first != second

    def test_rejects_more_than_max_files(self, tmp_path):
        items = [
            {"name": f"f{i}.txt", "mime": "text/plain", "data": _b64(b"x")}
            for i in range(MAX_FILES + 2)
        ]
        saved, errors = save_attachments(items, tmp_path)
        assert len(saved) == MAX_FILES
        assert any("最多" in e for e in errors)

    def test_rejects_oversize(self, tmp_path):
        saved, errors = save_attachments(
            [{"name": "big.bin", "mime": "application/octet-stream",
              "data": _b64(b"0" * (MAX_BYTES + 1))}], tmp_path)
        assert saved == []
        assert any("MB" in e for e in errors)

    def test_rejects_bad_base64_and_empty(self, tmp_path):
        saved, errors = save_attachments(
            [{"name": "bad.png", "mime": "image/png", "data": "!!!not-base64!!!"},
             {"name": "empty.txt", "mime": "text/plain", "data": ""}], tmp_path)
        assert saved == []
        assert len(errors) == 2

    def test_no_attachments_is_a_noop(self, tmp_path):
        assert save_attachments(None, tmp_path) == ([], [])
        assert not (tmp_path / ".zouwucode").exists()


class TestPromptHelpers:
    def test_note_lists_paths_and_warnings(self):
        saved = [{"name": "a.png", "mime": "image/png", "size": 10,
                  "relative": ".zouwucode/uploads/a.png", "path": "/x/a.png"}]
        note = attachment_note(saved, ["b.png: 超过 8 MB，已跳过"])
        assert ".zouwucode/uploads/a.png" in note
        assert "a.png" in note and "8 MB" in note
        assert note.startswith("\n\n[attachments")

    def test_note_is_empty_without_uploads(self):
        assert attachment_note([], []) == ""

    def test_image_parts_only_inline_images(self, tmp_path):
        saved, _ = save_attachments([
            {"name": "a.png", "mime": "image/png", "data": _b64(PNG)},
            {"name": "b.txt", "mime": "text/plain", "data": _b64(b"hi")},
        ], tmp_path)
        parts = image_parts(saved)
        assert len(parts) == 1
        assert parts[0]["type"] == "image_url"
        assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")


class _FakeStats:
    hit_rate = 0.0
    total_cost = 0.0


class _Provider:
    def __init__(self, vision=False):
        self.supports_vision = vision


class _RecordingEngine:
    """Captures the messages the server hands to the engine."""

    on_thinking_delta = None
    on_content_delta = None
    on_tool_event = None
    stats = _FakeStats()

    def __init__(self, vision=False):
        self.provider = _Provider(vision)
        self.messages = []

    async def run(self, messages, tools=None):
        self.messages = messages
        from zouwucode.engine.providers.base import ModelResponse
        return ModelResponse(content="ok", thinking="", cache_hit=False, usage={})


class _FakeTools:
    def get_schemas(self):
        return []


class _Memory:
    def get_full_context(self):
        return ""


class _Coordinator:
    project_memory = _Memory()


class TestServerWiring:
    def _server(self, vision=False):
        from zouwucode.config import ZOUWUCODEConfig
        config = ZOUWUCODEConfig()
        config.show_thinking = False
        engine = _RecordingEngine(vision)
        srv = web_server.WebUIServer(
            engine=engine, tools=_FakeTools(), coordinator=_Coordinator(),
            config=config, modules=None)
        return srv, engine

    def _stream(self, srv, payload):
        class Writer:
            def __init__(self):
                self.chunks = []

            def write(self, data):
                self.chunks.append(data)

            async def drain(self):
                pass

            def close(self):
                pass

        writer = Writer()
        asyncio.run(srv._handle_chat_stream(writer, json.dumps(payload)))
        text = b"".join(writer.chunks).decode("utf-8")
        events = [json.loads(line[6:]) for line in text.splitlines()
                  if line.startswith("data: ")]
        return events

    def test_text_model_gets_workspace_paths(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        srv, engine = self._server(vision=False)
        events = self._stream(srv, {
            "message": "看看这张图",
            "attachments": [{"name": "shot.png", "mime": "image/png",
                             "data": _b64(PNG)}],
        })
        assert events[0]["type"] == "attachments"
        assert events[0]["saved"][0]["relative"].startswith(".zouwucode/uploads/")
        content = engine.messages[-1]["content"]
        assert isinstance(content, str), "文本模型不应收到图片内容块"
        assert "看看这张图" in content
        assert ".zouwucode/uploads/" in content
        assert (tmp_path / ".zouwucode/uploads").is_dir()

    def test_vision_model_gets_inline_image(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        srv, engine = self._server(vision=True)
        self._stream(srv, {
            "message": "描述一下",
            "attachments": [{"name": "shot.png", "mime": "image/png",
                             "data": _b64(PNG)}],
        })
        content = engine.messages[-1]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text" and "描述一下" in content[0]["text"]
        assert any(p["type"] == "image_url" for p in content[1:])

    def test_no_attachments_keeps_plain_string(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        srv, engine = self._server()
        events = self._stream(srv, {"message": "hi"})
        assert all(e["type"] != "attachments" for e in events)
        assert engine.messages[-1]["content"] == "hi"

    def test_non_streaming_endpoint_accepts_attachments(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        srv, engine = self._server()
        raw = asyncio.run(srv._route("POST", "/api/chat", json.dumps({
            "message": "read it",
            "attachments": [{"name": "a.txt", "mime": "text/plain",
                             "data": _b64(b"hello")}],
        })))
        body = json.loads(raw.split("\r\n\r\n", 1)[1])
        assert body["attachments"][0]["name"] == "a.txt"
        assert body["attachment_errors"] == []
        assert ".zouwucode/uploads/" in engine.messages[-1]["content"]

    def test_socket_layer_carries_attachment_body(self, tmp_path, monkeypatch):
        """The real request parser must assemble the base64 body correctly."""
        monkeypatch.chdir(tmp_path)
        srv, _ = self._server()
        body = json.dumps({
            "message": "看图",
            "attachments": [{"name": "a.png", "mime": "image/png",
                             "data": _b64(PNG)}],
        }).encode("utf-8")
        raw = (b"POST /api/chat HTTP/1.1\r\nHost: t\r\n"
               b"Content-Type: application/json\r\n"
               b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)

        class Reader:
            def __init__(self, data):
                self.data = data

            async def read(self, n):
                chunk, self.data = self.data[:n], self.data[n:]
                return chunk

        class Writer:
            def __init__(self):
                self.chunks = []

            def write(self, data):
                self.chunks.append(data)

            async def drain(self):
                pass

            def close(self):
                pass

        writer = Writer()
        asyncio.run(srv.handle_request(Reader(raw), writer))
        text = b"".join(writer.chunks).decode("utf-8")
        assert text.startswith("HTTP/1.1 200")
        payload = json.loads(text.split("\r\n\r\n", 1)[1])
        assert payload["attachments"][0]["name"] == "a.png"

    def test_upload_errors_are_reported_not_fatal(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        srv, engine = self._server()
        events = self._stream(srv, {
            "message": "x",
            "attachments": [{"name": "bad.png", "mime": "image/png",
                             "data": "%%%"}],
        })
        attach = next(e for e in events if e["type"] == "attachments")
        assert attach["saved"] == [] and attach["errors"]
        assert events[-1]["type"] == "done", "上传失败不应中断这一轮"


class TestFrontendContract:
    def test_composer_has_attach_controls(self):
        ids = set(re.findall(r'id="([^"]+)"', INDEX))
        for required in ("attach-btn", "attach-input", "attach-preview"):
            assert required in ids, f"缺少 DOM 节点 #{required}"
        assert 'type="file"' in INDEX and "multiple" in INDEX

    def test_every_js_selector_has_a_node(self):
        ids = set(re.findall(r'id="([^"]+)"', INDEX))
        wanted = set(re.findall(r'\$\("#([\w-]+)"\)', JS))
        assert not sorted(wanted - ids)

    def test_sends_attachments_with_the_message(self):
        assert "/api/chat/stream" in JS
        assert re.search(r"attachments:\s*files\.map", JS), "发送时应把附件带上"
        assert "readAsDataURL" in JS, "附件需以 base64 发送"
        assert "FileReader" in JS

    def test_drag_drop_and_paste_supported(self):
        assert '"drop"' in JS and "dataTransfer" in JS
        assert '"paste"' in JS and "clipboardData" in JS
        assert "dragging" in JS and ".composer.dragging" in CSS

    def test_ui_limits_match_backend(self):
        assert "MAX_ATTACH = 5" in JS
        assert "8 * 1024 * 1024" in JS
        assert 'case "attachments"' in JS, "前端应处理 attachments 事件"
