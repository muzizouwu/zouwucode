"""v1.15.0 ecosystem round: DeepSeek vision, global skills, web persistence.

Three gaps closed together:
- DeepSeek V4.1 Flash is natively multimodal but the provider still claimed
  ``supports_vision = False``, so images were only referenced by path.
- Skills were project-only; other agents ship a user-level skills library.
- Web UI turns never reached SessionManager, so a browser restart lost them.
"""

import asyncio
import base64
import json

from zouwucode.attachments import MAX_TOTAL_BYTES, save_attachments
from zouwucode.engine.providers.deepseek import DeepSeekProvider
from zouwucode.skills.manager import SkillsManager
from zouwucode.webui import server as web_server


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


class TestDeepSeekVision:
    def _provider(self, model):
        return DeepSeekProvider({"api_key": "k", "model": model})

    def test_v4_models_accept_images(self):
        for model in ("deepseek-flash", "deepseek-v4-flash",
                      "deepseek-v4-pro", "deepseek-vl2"):
            assert self._provider(model).supports_vision, model

    def test_unknown_text_model_stays_conservative(self):
        for model in ("deepseek-chat", "deepseek-coder", "some-local-model"):
            assert not self._provider(model).supports_vision, model

    def test_default_model_is_vision_capable(self):
        assert DeepSeekProvider({"api_key": "k"}).supports_vision


class TestGlobalSkills:
    def test_user_skills_are_listed_and_loadable(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        global_dir = home / ".zouwucode" / "skills"
        global_dir.mkdir(parents=True)
        (global_dir / "shared.md").write_text("global body", encoding="utf-8")

        project = tmp_path / "proj"
        project.mkdir()
        # Path.home() is what the manager consults for the user-level library.
        monkeypatch.setattr("zouwucode.skills.manager.Path.home", lambda: home)

        manager = SkillsManager(project)
        assert manager.global_dir == global_dir
        assert "shared" in manager.list_available()
        assert manager.load("shared") == "global body"

    def test_project_skill_shadows_global(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setattr("zouwucode.skills.manager.Path.home", lambda: home)
        gdir = home / ".zouwucode" / "skills"
        gdir.mkdir(parents=True)
        (gdir / "style.md").write_text("global", encoding="utf-8")

        project = tmp_path / "proj"
        pdir = project / ".zouwucode" / "skills"
        pdir.mkdir(parents=True)
        (pdir / "style.md").write_text("project", encoding="utf-8")

        manager = SkillsManager(project)
        assert manager.list_available().count("style") == 1
        assert manager.load("style") == "project"

    def test_missing_skill_lists_every_candidate(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setattr("zouwucode.skills.manager.Path.home", lambda: home)
        manager = SkillsManager(tmp_path)
        try:
            manager.load("nope")
        except FileNotFoundError as exc:
            assert "nope" in str(exc)
        else:
            raise AssertionError("missing skill must raise")


class TestAttachmentTotalCap:
    def test_total_cap_rejects_extra_files(self, tmp_path):
        chunk = b"x" * (7 * 1024 * 1024)      # 7 MiB each; 5 would be 35 MiB
        items = [{"name": f"f{i}.bin", "mime": "application/octet-stream",
                  "data": _b64(chunk)} for i in range(5)]
        saved, errors = save_attachments(items, tmp_path)
        assert len(saved) == 4, "32 MiB / 7 MiB → four files, then the cap"
        assert any("合计" in e for e in errors)

    def test_cap_is_under_the_provider_body_limit(self):
        assert MAX_TOTAL_BYTES * 4 / 3 < 48 * 1024 * 1024


class _Stats:
    hit_rate = 0.0
    total_cost = 0.0


class _Engine:
    on_thinking_delta = None
    on_content_delta = None
    on_tool_event = None
    stats = _Stats()

    def __init__(self):
        self.provider = type("P", (), {"supports_vision": True})()

    async def run(self, messages, tools=None):
        from zouwucode.engine.providers.base import ModelResponse
        return ModelResponse(content="answer", thinking="", usage={})


class _Tools:
    def get_schemas(self):
        return []


class _Memory:
    def get_full_context(self):
        return ""


class _Coordinator:
    project_memory = _Memory()


class _Sessions:
    def __init__(self):
        self.current_session_id = "s-1"
        self.turns = []

    def log_turn(self, entry):
        self.turns.append(entry)


class TestWebTurnPersistence:
    def _server(self, sessions):
        from zouwucode.config import ZOUWUCODEConfig
        config = ZOUWUCODEConfig()
        config.show_thinking = False
        srv = web_server.WebUIServer(
            engine=_Engine(), tools=_Tools(), coordinator=_Coordinator(),
            config=config, modules=None, sessions_manager=sessions)
        return srv

    def _run(self, srv, payload):
        class Writer:
            def __init__(self):
                self.chunks = []

            def write(self, data):
                self.chunks.append(data)

            async def drain(self):
                pass

            def close(self):
                pass

        asyncio.run(srv._handle_chat_stream(Writer(), json.dumps(payload)))

    def test_stream_turn_is_logged(self):
        sessions = _Sessions()
        self._run(self._server(sessions), {"message": "hello"})
        roles = [t["role"] for t in sessions.turns]
        assert roles == ["user", "assistant"]
        assert sessions.turns[0]["content"] == "hello"
        assert sessions.turns[1]["content"] == "answer"

    def test_vision_images_are_not_written_to_the_session(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sessions = _Sessions()
        self._run(self._server(sessions), {
            "message": "看图",
            "attachments": [{"name": "a.png", "mime": "image/png",
                             "data": _b64(b"\x89PNG\r\n\x1a\n" + b"0" * 16)}],
        })
        logged = sessions.turns[0]["content"]
        assert "看图" in logged and ".zouwucode/uploads/" in logged
        assert "base64" not in logged, "图片 base64 不应写进会话文件"
