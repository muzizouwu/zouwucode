"""Local web server providing a DeepCode-inspired browser-based UI for ZOUWUCODE.

Usage:
    zouwucode --web              # Start with web UI on port 8080
    zouwucode --web --port 3000  # Custom port
"""
import logging

logger = logging.getLogger("zouwucode.webui.server")


import asyncio
import json
import time
import traceback

from ..engine.loop import EngineLoop, TaskInterrupted
from ..attachments import attachment_note, image_parts, save_attachments
from ..errors import describe_exc
from ..tools.registry import ToolRegistry
from ..agent.coordinator import AgentCoordinator
from ..config import ZOUWUCODEConfig


from pathlib import Path

# ── Static GUI assets ──────────────────────────────────────────────────────
# The GUI ships as plain files (HTML/CSS/JS) so it works fully offline: no
# CDN, no build step, no framework. Only files directly under static/ are
# reachable, which keeps this HTTP layer from becoming a file reader.
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
}


def read_static(name: str):
    """Return ``(body, content_type)`` for a shipped asset, else ``None``."""
    if not name or name.startswith(".") or "/" in name or "\\" in name:
        return None
    target = (_STATIC_DIR / name).resolve()
    try:
        target.relative_to(_STATIC_DIR)
    except ValueError:
        return None
    if not target.is_file():
        return None
    ctype = _CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
    try:
        return target.read_text(encoding="utf-8"), ctype
    except OSError:
        return None


def page_html() -> str:
    """The GUI shell; empty when the package was installed without assets."""
    asset = read_static("index.html")
    return asset[0] if asset else ""


# Kept as a module constant for backwards compatibility with importers.
HTML_TEMPLATE = page_html()


class WebUIServer:
    """Simple local web server for browser-based UI."""

    def __init__(
        self,
        engine: EngineLoop,
        tools: ToolRegistry,
        coordinator: AgentCoordinator,
        config: ZOUWUCODEConfig,
        port: int = 8080,
        host: str = "127.0.0.1",
        skills_manager=None,
        rule_loader=None,
        modules=None,
        sessions_manager=None,
        subagent_manager=None,
        bus=None,
        app=None,
    ):
        self.engine = engine
        self.tools = tools
        self.coordinator = coordinator
        self.config = config
        self.port = port
        self.host = host
        self._server = None
        self._skills = skills_manager
        self._rules = rule_loader
        self.modules = modules
        self.sessions_manager = sessions_manager
        # Sub-agent status (exposed to the browser so /agents reaches parity).
        self.subagent_manager = subagent_manager
        # Cross-session messaging bus (may be None when disabled).
        self.bus = bus
        # The owning application (for /resume etc.); optional so tests can
        # construct the server without a full app.
        self.app = app
        # opencode-style tool-call presentation ("→ Read file …")
        self._tool_buffer: list[dict] = []
        # The engine owns a single PrefixCache and single-slot streaming
        # callbacks — only one chat turn may run at a time.
        self._chat_busy = False
        # Interactive approvals (agent mode): decisions arrive via
        # POST /api/approve and resolve one pending future per tool call.
        self._approval_futures: dict = {}
        self._approval_queue = None
        self._approved_always: set = set()
        if self.coordinator is not None:
            self.coordinator.approval_callback = self._web_approve
        if self.engine is not None:
            self.engine.on_tool_event = self._on_tool_event

    def _on_tool_event(self, tool_call) -> None:
        """Buffer tool calls for the current /api/chat request."""
        try:
            name = getattr(tool_call, "name", "tool")
            args = getattr(tool_call, "arguments", "") or ""
            if isinstance(args, dict):
                args = json.dumps(args, ensure_ascii=False)
            self._tool_buffer.append({"name": name, "arguments": str(args)[:200]})
        except Exception as _exc:
            logger.debug("swallowed %s: %s", type(_exc).__name__, _exc)

    @staticmethod
    def _plain_text(content) -> str:
        """Text-only form of a user turn (inline image bytes are not logged)."""
        if isinstance(content, list):
            return "\n".join(
                str(part.get("text", "")) for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ).strip()
        return str(content or "")

    def _log_exchange(self, user_content, assistant_text: str) -> None:
        """Persist the turn so resume/sidebar see Web UI conversations.

        Web turns used to bypass SessionManager entirely: the transcript in the
        browser was the only copy, so a restart lost the conversation and
        /api/sessions showed zero turns. Inline images are stripped to keep the
        JSON small; the attachments themselves stay in the workspace.
        """
        mgr = self._sessions()
        if mgr is None or not getattr(mgr, "current_session_id", None):
            return
        try:
            mgr.log_turn({"role": "user", "content": self._plain_text(user_content)})
            if assistant_text:
                mgr.log_turn({"role": "assistant", "content": assistant_text})
        except Exception as exc:  # logging must never break a finished turn
            logger.debug("session log failed: %s", describe_exc(exc))

    def _user_content(self, message: str, attachments) -> tuple[object, dict]:
        """Persist uploads and build the user turn's content.

        Uploads land in the workspace first, so text-only endpoints can still
        act on them through tools. Vision-capable providers (``deepseek-flash``
        and other multimodal models) get the images inline as data URLs too.
        """
        text = str(message or "")
        saved, errors = save_attachments(attachments, Path.cwd())
        note = attachment_note(saved, errors)
        if note:
            text = (text.rstrip() + note) if text.strip() else note.strip()
        info = {
            "saved": [{k: v for k, v in item.items() if k != "path"} for item in saved],
            "errors": errors,
        }
        provider = getattr(self.engine, "provider", None)
        if saved and getattr(provider, "supports_vision", False):
            parts = image_parts(saved)
            if parts:
                return [{"type": "text", "text": text}] + parts, info
        return text, info

    async def _web_approve(self, tool_call, context) -> bool:
        """Ask the browser (y/n/a) before a tool runs in agent mode.

        Falls back to DENY when there is no open chat stream to ask through
        — an unapproved call must never silently execute.
        """
        name = getattr(tool_call, "name", "tool")
        if name in self._approved_always:
            return True
        queue = self._approval_queue
        if queue is None:
            # No open SSE stream to ask through (e.g. the legacy non-stream
            # /api/chat endpoint). Raising makes the coordinator report
            # "approval prompt failed — NOT executed" instead of a silent
            # denial that would look like the user answered no.
            raise RuntimeError(
                "no interactive approval channel for this request — use the "
                "streaming chat endpoint in agent mode")
        args = getattr(tool_call, "arguments", "") or ""
        if isinstance(args, dict):
            args = json.dumps(args, ensure_ascii=False)
        call_id = str(getattr(tool_call, "id", "") or id(tool_call))
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._approval_futures[call_id] = fut
        try:
            queue.put_nowait({
                "type": "approval_request",
                "id": call_id,
                "tool": name,
                "preview": str(args)[:300],
            })
            decision = await asyncio.wait_for(fut, timeout=300)
        except asyncio.TimeoutError:
            decision = "n"
        finally:
            self._approval_futures.pop(call_id, None)
        if decision == "a":
            self._approved_always.add(name)
            return True
        return decision == "y"

    async def _sse_send(self, writer, obj: dict) -> None:
        """Write one SSE `data:` event, swallowing broken-pipe errors."""
        try:
            payload = json.dumps(obj, ensure_ascii=False)
            writer.write(f"data: {payload}\n\n".encode("utf-8"))
            await writer.drain()
        except Exception as _exc:
            logger.debug("swallowed %s: %s", type(_exc).__name__, _exc)

    async def _handle_chat_stream(self, writer, body: str) -> None:
        """SSE streaming chat handler — streams thinking deltas live.

        Events:
          {"type": "thinking", "delta": "..."}  incremental reasoning text
          {"type": "done", ...}                 final content / tools / stats
          {"type": "error", "error": "..."}     turn failed
        """
        try:
            data = json.loads(body)
            message = data.get("message", "")
            attachments = data.get("attachments") or []
        except json.JSONDecodeError:
            await self._sse_send(writer, {"type": "error", "error": "Invalid JSON"})
            writer.close()
            return

        # One chat turn at a time — the engine is a shared singleton.
        if self._chat_busy:
            await self._sse_send(writer, {
                "type": "error",
                "error": "另一个任务正在执行中，请等待完成或先按 Esc 打断当前任务。",
            })
            writer.close()
            return
        self._chat_busy = True

        # SSE headers — no Content-Length, connection stays open while streaming
        writer.write((
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/event-stream\r\n"
            "Cache-Control: no-cache\r\n"
            "Connection: keep-alive\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "\r\n"
        ).encode("utf-8"))
        await writer.drain()

        # Modules may intercept messages ("ultrawork …" / "ulw …" prefix)
        self._tool_buffer.clear()
        if self.modules is not None:
            module_result = await self.modules.dispatch_message(message)
            if module_result is not None:
                await self._sse_send(writer, {
                    "type": "done",
                    "module": module_result.get("title", "module"),
                    "content": module_result.get("content", ""),
                    "plan": module_result.get("plan_name"),
                    "tasks_completed": module_result.get("tasks_completed"),
                    "tasks_total": module_result.get("tasks_total"),
                })
                writer.close()
                return

        # Uploads → workspace files (plus inline images for vision models).
        user_content, attach_info = self._user_content(message, attachments)
        if attach_info["saved"] or attach_info["errors"]:
            await self._sse_send(writer, {"type": "attachments", **attach_info})

        queue: asyncio.Queue = asyncio.Queue()
        # Approvals for this turn are asked through the same SSE stream.
        self._approval_queue = queue
        show_thinking = getattr(self.config, "show_thinking", True)

        def _on_thinking(delta: str) -> None:
            """Engine callback (sync, same event loop) → SSE queue."""
            if show_thinking:
                try:
                    queue.put_nowait(delta)
                except Exception as _exc:
                    logger.debug("swallowed %s: %s", type(_exc).__name__, _exc)

        streamed_body = {"any": False}

        def _on_content(delta: str) -> None:
            """Stream the assistant BODY, not just thinking (opencode-style)."""
            streamed_body["any"] = True
            try:
                queue.put_nowait({"type": "content", "delta": delta})
            except Exception as _exc:
                logger.debug("swallowed %s: %s", type(_exc).__name__, _exc)

        self.engine.on_thinking_delta = _on_thinking
        self.engine.on_content_delta = _on_content
        try:
            pm = getattr(self.coordinator, "project_memory", None)
            project_ctx = pm.get_full_context() if pm else ""
            messages = []
            if project_ctx:
                messages.append({"role": "system", "content": project_ctx})
            messages.append({"role": "user", "content": user_content})

            task = asyncio.create_task(self.engine.run(
                messages=messages,
                tools=self.tools.get_schemas(),
            ))

            # Drain the delta queue until the turn completes
            while True:
                if task.done() and queue.empty():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.2)
                    if isinstance(item, dict):
                        await self._sse_send(writer, item)
                    else:
                        await self._sse_send(writer, {"type": "thinking", "delta": item})
                except asyncio.TimeoutError:
                    continue

            response = task.result()
            await self._sse_send(writer, {
                "type": "done",
                "content": response.content,
                "thinking": response.thinking,
                "streamed": streamed_body["any"],
                "tools": list(self._tool_buffer),
                "cache_hit": response.cache_hit,
                "stats": f"Cache: {self.engine.stats.hit_rate * 100:.0f}% | Cost: ${self.engine.stats.total_cost:.4f}",
            })
            # Persist after "done" reaches the browser: log_turn snapshots the
            # workspace, which must not delay the visible end of the turn.
            self._log_exchange(user_content, response.content)
        except TaskInterrupted as e:
            # User interrupted this turn — tell the client clearly (with
            # recovery options) instead of reporting a generic error.
            await self._sse_send(writer, {
                "type": "interrupted",
                "message": str(e),
            })
        except Exception as e:
            await self._sse_send(writer, {"type": "error", "error": describe_exc(e)})
        finally:
            self._chat_busy = False
            self._approval_queue = None
            self.engine.on_thinking_delta = None
            self.engine.on_content_delta = None
            writer.close()

    async def handle_request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle an HTTP request."""
        # Read the full request. The body may arrive in a later packet than
        # the headers, so keep reading until Content-Length bytes are received.
        request_data = b""
        try:
            while True:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=30)
                if not chunk:
                    break
                request_data += chunk
                if b"\r\n\r\n" in request_data:
                    head, _, _ = request_data.partition(b"\r\n\r\n")
                    clen = 0
                    for line in head.decode("utf-8", "replace").split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            try:
                                clen = int(line.split(":", 1)[1].strip())
                            except ValueError:
                                clen = 0
                    if len(request_data) >= len(head) + 4 + clen:
                        break
        except (asyncio.TimeoutError, Exception):
            writer.close()
            return

        request_str = request_data.decode("utf-8", errors="replace")

        if not request_str:
            writer.close()
            return

        lines = request_str.split("\r\n")
        if not lines:
            writer.close()
            return

        method_path = lines[0].split()
        if len(method_path) < 2:
            writer.close()
            return

        method = method_path[0]
        path = method_path[1]

        # Read body
        body = ""
        body_start = request_str.find("\r\n\r\n")
        if body_start >= 0:
            body = request_str[body_start + 4:]

        # Streaming SSE endpoint — writes chunks incrementally, keeps the
        # connection open until the turn finishes (opencode-style thinking).
        if path == "/api/chat/stream" and method == "POST":
            await self._handle_chat_stream(writer, body)
            return

        response = await self._route(method, path, body)

        writer.write(response.encode("utf-8"))
        await writer.drain()
        writer.close()

    def _sessions(self):
        """Session manager, whichever attribute the app wired."""
        mgr = getattr(self, "sessions_manager", None)
        if mgr is None:
            mgr = getattr(self.engine, "sessions", None)
        return mgr

    def _session_action(self, body: str, action: str) -> str:
        """delete / archive / unarchive a session by id (GUI management)."""
        mgr = self._sessions()
        if mgr is None:
            return self._json_response({"ok": False, "error": "sessions unavailable"}, 400)
        try:
            sid = (json.loads(body or "{}").get("id") or "").strip()
        except json.JSONDecodeError:
            return self._json_response({"ok": False, "error": "invalid JSON"}, 400)
        if not sid:
            return self._json_response({"ok": False, "error": "missing id"}, 400)
        if self._chat_busy:
            return self._json_response(
                {"ok": False, "error": "another task is running"}, 409)
        if action == "delete":
            ok = mgr.delete_session(sid)
        elif action == "archive":
            ok = mgr.archive_session(sid)
        else:
            ok = mgr.unarchive_session(sid)
        return self._json_response(
            {"ok": bool(ok)} if ok else {"ok": False, "error": "session not found"},
            200 if ok else 404)

    async def _route(self, method: str, path: str, body: str) -> str:
        """Route an HTTP request to the appropriate handler."""
        if path == "/" or path == "/index.html":
            page = page_html()
            if not page:
                return self._html_response(
                    "<h1>GUI assets missing</h1><p>Reinstall zouwucode: the "
                    "package must ship zouwucode/webui/static/.</p>", 500)
            return self._html_response(page)

        elif path.startswith("/static/") and method == "GET":
            asset = read_static(path[len("/static/"):])
            if asset is None:
                return self._json_response({"error": "asset not found"}, 404)
            asset_body, asset_type = asset
            payload = asset_body.encode("utf-8")
            return (
                "HTTP/1.1 200 OK\r\n"
                f"Content-Type: {asset_type}\r\n"
                f"Content-Length: {len(payload)}\r\n"
                "Cache-Control: no-cache\r\n"
                "Access-Control-Allow-Origin: *\r\n"
                "Connection: close\r\n"
                "\r\n" + asset_body)

        elif path == "/api/chat" and method == "POST":
            try:
                data = json.loads(body)
                message = data.get("message", "")
                attachments = data.get("attachments") or []
            except json.JSONDecodeError:
                return self._json_response({"error": "Invalid JSON"}, 400)

            # Modules may intercept messages ("ultrawork …" / "ulw …" prefix)
            self._tool_buffer.clear()
            if self.modules is not None:
                module_result = await self.modules.dispatch_message(message)
                if module_result is not None:
                    return self._json_response({
                        "content": module_result.get("content", ""),
                        "details": module_result.get("details") or [],
                        "plan": module_result.get("plan_name"),
                        "tasks_completed": module_result.get("tasks_completed"),
                        "tasks_total": module_result.get("tasks_total"),
                        "module": module_result.get("title", "module"),
                    })

            # One chat turn at a time — the engine is a shared singleton.
            if self._chat_busy:
                return self._json_response(
                    {"error": "另一个任务正在执行中，请等待完成或先按 Esc 打断当前任务。"},
                    409,
                )
            self._chat_busy = True
            try:
                project_ctx = self.coordinator.project_memory.get_full_context() if hasattr(self.coordinator, 'project_memory') else ""
                user_content, attach_info = self._user_content(message, attachments)

                messages = []
                if project_ctx:
                    messages.append({"role": "system", "content": project_ctx})
                messages.append({"role": "user", "content": user_content})

                response = await self.engine.run(
                    messages=messages,
                    tools=self.tools.get_schemas(),
                )
                self._log_exchange(user_content, response.content)

                return self._json_response({
                    "content": response.content,
                    "thinking": response.thinking,
                    "tools": list(self._tool_buffer),
                    "cache_hit": response.cache_hit,
                    "attachments": attach_info["saved"],
                    "attachment_errors": attach_info["errors"],
                    "stats": f"Cache: {self.engine.stats.hit_rate * 100:.0f}% | Cost: ${self.engine.stats.total_cost:.4f}",
                })
            except Exception as e:
                err_msg = describe_exc(e)
                tb = traceback.format_exc()
                return self._json_response({"error": err_msg, "detail": tb}, 500)
            finally:
                self._chat_busy = False

        elif path == "/api/approve" and method == "POST":
            # Interactive approval decision from the browser (agent mode).
            try:
                data = json.loads(body)
                call_id = str(data.get("id", ""))
                decision = str(data.get("decision", "n")).lower()[:1]
                if decision not in ("y", "a", "n"):
                    decision = "n"
                fut = self._approval_futures.get(call_id)
                if fut is None or fut.done():
                    return self._json_response({"resolved": False}, 404)
                fut.set_result(decision)
                return self._json_response({"resolved": True, "decision": decision})
            except Exception as e:
                return self._json_response({"error": str(e)}, 400)

        elif path == "/api/interrupt" and method == "POST":
            # Interrupt the currently running task (TUI/WebUI parity).
            try:
                ok = self.engine.request_interrupt(reason="WebUI interrupt button")
            except Exception as e:
                return self._json_response({"error": str(e)}, 500)
            if ok:
                return self._json_response({"interrupting": True})
            return self._json_response(
                {"interrupting": False, "message": "当前没有正在执行的任务"}, 409
            )

        elif path == "/api/mode" and method == "POST":
            try:
                data = json.loads(body)
                mode = data.get("mode", "agent")
                self.engine.set_mode(mode)
                return self._json_response({"mode": mode})
            except Exception as e:
                return self._json_response({"error": str(e)}, 400)

        elif path == "/api/reasoning" and method == "POST":
            try:
                data = json.loads(body)
                level = data.get("reasoning", "medium")
                self.config.reasoning_intensity = level
                self.engine.set_reasoning_intensity(level)
                return self._json_response({"reasoning": level})
            except Exception as e:
                return self._json_response({"error": str(e)}, 400)

        elif path == "/api/thinking" and method == "POST":
            # Toggle live thinking display (mirrors /thinking on CLI/TUI)
            self.config.show_thinking = not getattr(self.config, "show_thinking", True)
            return self._json_response({"show_thinking": self.config.show_thinking})

        elif path == "/api/cache" and method == "GET":
            return self._json_response(self.engine.get_cache_summary())

        elif path == "/api/status" and method == "GET":
            skills_loaded = list(self._skills.list_loaded()) if self._skills else []
            has_rules = self._rules.exists() if self._rules else False
            # Context token estimate from engine stats
            ctx_tokens = getattr(getattr(self.engine, "stats", None),
                                 "total_prompt_tokens", 0)
            return self._json_response({
                "mode": self.engine.mode,
                "running": self.engine.is_running,
                "reasoning": self.config.reasoning_intensity,
                "show_thinking": getattr(self.config, "show_thinking", True),
                "engine": "running",
                "cache_hit_rate": f"{self.engine.stats.hit_rate * 100:.1f}%",
                "total_requests": self.engine.stats.total_requests,
                "total_cost": f"${self.engine.stats.total_cost:.4f}",
                "context_tokens": ctx_tokens,
                "max_context_tokens": getattr(
                    getattr(self.config, "context", None), "max_context_tokens", 1048576),
                "rules_loaded": has_rules,
                "skills_loaded": skills_loaded,
                "modules": self.modules.loaded() if self.modules else [],
            })

        elif path == "/api/rules" and method == "GET":
            if self._rules:
                return self._json_response({"content": self._rules.load()})
            return self._json_response({"content": ""})

        elif path == "/api/skills" and method == "GET":
            if self._skills:
                return self._json_response({
                    "available": self._skills.list_available(),
                    "loaded": self._skills.list_loaded(),
                })
            return self._json_response({"available": [], "loaded": []})

        elif path == "/api/skills" and method == "POST":
            try:
                data = json.loads(body)
                action = data.get("action", "")
                name = data.get("name", "")
                if not self._skills:
                    return self._json_response({"error": "Skills system not available"}, 400)
                if action == "load":
                    self._skills.load(name)
                    return self._json_response({"success": True, "loaded": self._skills.list_loaded()})
                elif action == "unload":
                    self._skills.unload(name)
                    return self._json_response({"success": True, "loaded": self._skills.list_loaded()})
                else:
                    return self._json_response({"error": f"Unknown action: {action}"}, 400)
            except Exception as e:
                return self._json_response({"error": str(e)}, 400)

        elif path == "/api/sessions" and method == "GET":
            sessions_list = []
            manager = getattr(self, "sessions_manager", None)
            if manager is None:
                manager = getattr(self.engine, "sessions", None)
            if manager is not None:
                for s in manager.list_sessions():
                    sessions_list.append({
                        "id": s.get("id", ""),
                        # v1.13 auto-titles never reached the sidebar because
                        # this route dropped them; keep the label here.
                        "title": s.get("title") or s.get("id", ""),
                        "turns": s.get("turns", 0),
                        "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(s.get("updated", 0))),
                        "project_root": s.get("project_root", ""),
                    })
            return self._json_response({"sessions": sessions_list})

        elif path == "/api/session/current" and method == "GET":
            mgr = getattr(self, "sessions_manager", None)
            sid = getattr(mgr, "current_session_id", None) if mgr else None
            turns = mgr.current_log() if mgr else []
            return self._json_response({
                "session_id": sid,
                "turns": [{"role": t.get("role"), "content": t.get("content", "")}
                          for t in turns if t.get("role") in ("user", "assistant")],
            })

        elif path == "/api/session/resume" and method == "POST":
            app = getattr(self, "app", None)
            if app is None or not hasattr(app, "resume_session"):
                return self._json_response({"ok": False, "error": "resume unavailable"}, 400)
            try:
                sid = (json.loads(body or "{}").get("id") or "").strip()
            except json.JSONDecodeError:
                return self._json_response({"ok": False, "error": "invalid JSON"}, 400)
            if not sid:
                return self._json_response({"ok": False, "error": "missing id"}, 400)
            if self._chat_busy:
                return self._json_response({"ok": False, "error": "another task is running"}, 409)
            turns = app.resume_session(sid)
            if not turns:
                return self._json_response({"ok": False, "error": "session not found"}, 404)
            return self._json_response({
                "ok": True, "session_id": sid,
                "turns": [{"role": t.get("role"), "content": t.get("content", "")}
                          for t in turns if t.get("role") in ("user", "assistant")],
            })

        elif path == "/api/sessions/archived" and method == "GET":
            mgr = self._sessions()
            return self._json_response({
                "sessions": mgr.list_archived() if mgr is not None else []})

        elif path == "/api/session/delete" and method == "POST":
            return self._session_action(body, "delete")

        elif path == "/api/session/archive" and method == "POST":
            return self._session_action(body, "archive")

        elif path == "/api/session/unarchive" and method == "POST":
            return self._session_action(body, "unarchive")

        elif path == "/api/session/fork" and method == "POST":
            mgr = self._sessions()
            if mgr is None:
                return self._json_response({"ok": False, "error": "sessions unavailable"}, 400)
            try:
                data = json.loads(body or "{}")
            except json.JSONDecodeError:
                return self._json_response({"ok": False, "error": "invalid JSON"}, 400)
            sid = (data.get("id") or "").strip()
            if not sid:
                return self._json_response({"ok": False, "error": "missing id"}, 400)
            turn = data.get("turn")
            forked = mgr.fork_session(sid, at_turn=turn)
            if forked is None:
                return self._json_response(
                    {"ok": False, "error": "session not found"}, 404)
            return self._json_response({"ok": True, **forked})

        elif path == "/api/session/rename" and method == "POST":
            mgr = self._sessions()
            if mgr is None:
                return self._json_response({"ok": False, "error": "sessions unavailable"}, 400)
            try:
                data = json.loads(body or "{}")
            except json.JSONDecodeError:
                return self._json_response({"ok": False, "error": "invalid JSON"}, 400)
            sid = (data.get("id") or "").strip()
            title = " ".join(str(data.get("title") or "").split())
            if not sid or not title:
                return self._json_response(
                    {"ok": False, "error": "id and title are required"}, 400)
            ok = mgr.rename_session(sid, title)
            return self._json_response(
                {"ok": ok, "id": sid, "title": title} if ok
                else {"ok": False, "error": "session not found"},
                200 if ok else 404)

        elif path == "/api/workspaces" and method == "GET":
            mgr = self._sessions()
            current = str(Path.cwd().resolve())
            recent: list[str] = []
            for entry in (mgr.list_sessions() if mgr else []):
                root = entry.get("project_root") or ""
                if root and root not in recent and Path(root).is_dir():
                    recent.append(root)
            return self._json_response({
                "current": current,
                "recent": recent[:8],
            })

        elif path == "/api/workspace/switch" and method == "POST":
            if self._chat_busy:
                return self._json_response(
                    {"ok": False, "error": "another task is running"}, 409)
            app = getattr(self, "app", None)
            if app is None or not hasattr(app, "switch_workspace"):
                return self._json_response(
                    {"ok": False, "error": "workspace switching unavailable"}, 400)
            try:
                target = (json.loads(body or "{}").get("path") or "").strip()
            except json.JSONDecodeError:
                return self._json_response({"ok": False, "error": "invalid JSON"}, 400)
            if not target:
                return self._json_response(
                    {"ok": False, "error": "path is required"}, 400)
            result = app.switch_workspace(target)
            return self._json_response(result, 200 if result.get("ok") else 400)

        elif path == "/api/checkpoints" and method == "GET":
            mgr = getattr(self, "sessions_manager", None)
            cps = mgr.list_checkpoints() if mgr and hasattr(mgr, "list_checkpoints") else []
            log = mgr.current_log() if mgr else []
            return self._json_response({"checkpoints": cps, "messages": len(log)})

        elif path == "/api/rewind" and method == "POST":
            app = getattr(self, "app", None)
            if app is None or not hasattr(app, "rewind_to"):
                return self._json_response({"ok": False, "error": "rewind unavailable"}, 400)
            if self._chat_busy:
                return self._json_response({"ok": False, "error": "another task is running"}, 409)
            try:
                data = json.loads(body or "{}")
                index = int(data.get("index", 0))
                scope = str(data.get("scope", "both"))
            except (json.JSONDecodeError, ValueError, TypeError):
                return self._json_response({"ok": False, "error": "invalid request"}, 400)
            res = app.rewind_to(index, scope)
            if res.get("ok"):
                mgr = getattr(self, "sessions_manager", None)
                turns = mgr.current_log() if mgr else []
                res["turns"] = [{"role": t.get("role"), "content": t.get("content", "")}
                                for t in turns if t.get("role") in ("user", "assistant")]
            return self._json_response(res)

        elif path == "/api/rollback" and method == "POST":
            manager = getattr(self, "sessions_manager", None)
            if manager is None or getattr(manager, "snapshotter", None) is None:
                return self._json_response(
                    {"ok": False, "error": "rollback unavailable"}, 400)
            try:
                data = json.loads(body or "{}")
                steps = int(data.get("steps", 1) or 1)
            except (json.JSONDecodeError, ValueError, TypeError):
                return self._json_response(
                    {"ok": False, "error": "invalid steps"}, 400)
            return self._json_response(manager.rollback_workspace(steps))

        elif path == "/api/snapshots" and method == "GET":
            manager = getattr(self, "sessions_manager", None)
            snaps = manager.list_snapshots() if manager is not None else []
            return self._json_response({"snapshots": snaps})

        elif path == "/api/memory" and method == "GET":
            pm = getattr(self.coordinator, "project_memory", None)
            if pm is None:
                return self._json_response({"context": "", "goal": "", "decisions": []})
            return self._json_response({
                "context": pm.get_full_context(),
                "goal": pm.get_active_goal(),
                "decisions": pm.get_decisions(limit=10),
            })

        elif path == "/api/goal" and method == "POST":
            pm = getattr(self.coordinator, "project_memory", None)
            if pm is None:
                return self._json_response({"ok": False, "error": "no project memory"}, 400)
            try:
                goal = (json.loads(body or "{}").get("goal") or "").strip()
            except json.JSONDecodeError:
                return self._json_response({"ok": False, "error": "invalid JSON"}, 400)
            if goal:
                pm.set_goal(goal)
                pm.save_all()
            return self._json_response({"ok": True, "goal": pm.get_active_goal()})

        elif path == "/api/decide" and method == "POST":
            pm = getattr(self.coordinator, "project_memory", None)
            if pm is None:
                return self._json_response({"ok": False, "error": "no project memory"}, 400)
            try:
                text = (json.loads(body or "{}").get("text") or "").strip()
            except json.JSONDecodeError:
                return self._json_response({"ok": False, "error": "invalid JSON"}, 400)
            if text:
                pm.add_decision("Decision", text)
                pm.save_all()
            return self._json_response({"ok": True})

        elif path == "/api/oracle" and method == "POST":
            try:
                q = (json.loads(body or "{}").get("question") or "").strip()
            except json.JSONDecodeError:
                return self._json_response({"ok": False, "error": "invalid JSON"}, 400)
            if not q:
                return self._json_response({"ok": False, "error": "question required"}, 400)
            from ..runtime import ProviderRouter
            from ..tools.oracle_tool import ORACLE_SYSTEM
            provider = ProviderRouter(self.config).oracle_provider(
                getattr(self.engine, "provider", None))
            if provider is None:
                return self._json_response(
                    {"ok": False, "error": "oracle not configured "
                     "(router.enabled + router.oracle_provider)"}, 400)
            try:
                resp = await provider.chat(
                    [{"role": "system", "content": ORACLE_SYSTEM},
                     {"role": "user", "content": q}],
                    max_tokens=self.config.router.oracle_max_tokens,
                    temperature=self.config.router.oracle_temperature)
            except Exception as e:  # noqa: BLE001
                return self._json_response({"ok": False, "error": str(e)}, 500)
            return self._json_response({"ok": True,
                                        "model": getattr(provider, "model", ""),
                                        "answer": resp.content or ""})

        elif path == "/api/share" and method == "POST":
            app = getattr(self, "app", None)
            if app is not None and hasattr(app, "share_current_session"):
                return self._json_response(app.share_current_session())
            return self._json_response({"ok": False, "error": "sharing unavailable"}, 400)

        elif path == "/api/inbox" and method == "GET":
            bus = getattr(self, "bus", None)
            if bus is None:
                return self._json_response({"enabled": False, "messages": []})
            return self._json_response({"enabled": True, "messages": bus.drain()})

        elif path == "/api/peers" and method == "GET":
            bus = getattr(self, "bus", None)
            if bus is None:
                return self._json_response({"enabled": False, "peers": []})
            keys = ("agent", "role", "pid", "last_seen", "unread")
            peers = [{k: p.get(k) for k in keys} for p in bus.peers()]
            return self._json_response({"enabled": True, "peers": peers})

        elif path == "/api/msg" and method == "POST":
            bus = getattr(self, "bus", None)
            if bus is None:
                return self._json_response({"ok": False, "error": "messaging disabled"}, 400)
            try:
                data = json.loads(body or "{}")
            except json.JSONDecodeError:
                return self._json_response({"ok": False, "error": "invalid JSON"}, 400)
            to = (data.get("to") or "").strip()
            text = (data.get("text") or "").strip()
            if not to or not text:
                return self._json_response({"ok": False, "error": "to and text required"}, 400)
            return self._json_response({"ok": True, "id": bus.send(to, text), "to": to})

        elif path == "/api/usage" and method == "GET":
            engine = self.engine
            if hasattr(engine, "get_usage_summary"):
                return self._json_response(engine.get_usage_summary())
            return self._json_response({})

        elif path == "/api/context" and method == "GET":
            ctx = getattr(self.config, "context", None)
            return self._json_response({
                "prompt_tokens": getattr(getattr(self.engine, "stats", None),
                                         "total_prompt_tokens", 0),
                "max_context_tokens": getattr(ctx, "max_context_tokens", 1048576),
                "enabled": getattr(ctx, "enabled", False),
                "level": getattr(ctx, "level", "off"),
            })

        elif path == "/api/compress" and method == "GET":
            ctx = getattr(self.config, "context", None)
            return self._json_response({
                "enabled": getattr(ctx, "enabled", False),
                "level": getattr(ctx, "level", "balanced"),
                "max_context_tokens": getattr(ctx, "max_context_tokens", 1048576),
                "keep_last_n": getattr(ctx, "keep_last_n", 50),
            })

        elif path == "/api/agents" and method == "GET":
            mgr = self.subagent_manager
            agents = []
            if mgr is not None and hasattr(mgr, "status"):
                try:
                    agents = mgr.status()
                except Exception:  # noqa: BLE001 — status is best-effort
                    agents = []
            return self._json_response({"agents": agents})

        elif path == "/api/hello-my-zouwucode" and method == "POST":
            if self.modules is None:
                return self._json_response(
                    {"error": "No modules loaded"}, 400)
            module = self.modules.module("hello-my-zouwucode") if self.modules else None
            if module is None:
                return self._json_response(
                    {"error": "hello-my-zouwucode module is not loaded (config.hello_my_zouwucode.enabled=false)"}, 400)
            try:
                data = json.loads(body)
                action = data.get("action", "")
                task = data.get("task", "")

                if action == "ultrawork":
                    if not task:
                        return self._json_response({"error": "Missing task"}, 400)
                    result = await module.handle_command("/hello-ultrawork", task)
                elif action == "plan":
                    if not task:
                        return self._json_response({"error": "Missing task"}, 400)
                    result = await module.handle_command("/hello-plan", task)
                elif action == "start-work":
                    result = await module.handle_command("/hello-start-work")
                elif action == "agents":
                    result = await module.handle_command("/hello-agents")
                elif action == "status":
                    from hello_my_zouwucode.notepad import Notepad

                    payload = module.status_payload()
                    plan_name = payload.get("plan")
                    notepad_summary = ""
                    if plan_name:
                        notepad_summary = Notepad(plan_name, module.orchestrator.omo_dir).summary()
                    return self._json_response({
                        "boulder": payload.get("boulder", ""),
                        "plan_name": plan_name,
                        "notepad": notepad_summary,
                        "agents": payload.get("agents", 0),
                        "categories": payload.get("categories", 0),
                    })
                else:
                    return self._json_response({"error": f"Unknown action: {action}"}, 400)

                return self._json_response({
                    "success": True,
                    "content": (result or {}).get("content", ""),
                    "details": (result or {}).get("details") or [],
                    "plan": (result or {}).get("plan_name"),
                    "tasks_completed": (result or {}).get("tasks_completed"),
                    "tasks_total": (result or {}).get("tasks_total"),
                })
            except Exception as e:
                return self._json_response({"error": str(e)}, 500)

        return self._html_response("<h1>404 Not Found</h1>", 404)

    def _html_response(self, content: str, status: int = 200) -> str:
        return (
            f"HTTP/1.1 {status} {'OK' if status == 200 else 'Not Found'}\r\n"
            f"Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(content.encode('utf-8'))}\r\n"
            f"Access-Control-Allow-Origin: *\r\n"
            f"\r\n"
            f"{content}"
        )

    def _json_response(self, data: dict, status: int = 200) -> str:
        content = json.dumps(data, ensure_ascii=False)
        return (
            f"HTTP/1.1 {status} {'OK' if status == 200 else 'Error'}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(content.encode('utf-8'))}\r\n"
            f"Access-Control-Allow-Origin: *\r\n"
            f"\r\n"
            f"{content}"
        )

    async def start(self):
        """Start the web server."""
        self._server = await asyncio.start_server(
            self.handle_request,
            self.host,
            self.port,
        )
        addr = self._server.sockets[0].getsockname()
        print(f"\n  🌐 ZOUWUCODE Web UI: http://{addr[0]}:{addr[1]}")
        print(f"  Press Ctrl+C to stop\n")

        async with self._server:
            await self._server.serve_forever()

    async def stop(self):
        """Stop the web server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()