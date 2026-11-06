"""Local web server providing a DeepCode-inspired browser-based UI for ZOUWUCODE.

Usage:
    zouwucode --web              # Start with web UI on port 8080
    zouwucode --web --port 3000  # Custom port
"""
import logging

logger = logging.getLogger("zouwucode.webui.server")


import asyncio
import json
from pathlib import Path

from ..agent.coordinator import AgentCoordinator
from ..attachments import attachment_note, image_parts, save_attachments
from ..config import ZOUWUCODEConfig
from ..engine.loop import EngineLoop, TaskInterrupted
from ..errors import describe_exc
from ..tools.registry import ToolRegistry
from .routes import RoutesMixin

# ── Static GUI assets ──────────────────────────────────────────────────────
# The GUI ships as plain files (HTML/CSS/JS) so it works fully offline: no
# CDN, no build step, no framework. Only files directly under static/ are
# reachable, which keeps this HTTP layer from becoming a file reader.
_STATIC_DIR = Path(__file__).resolve().parent / "static"
# Attachments are capped at 32 MB raw in attachments.py; base64 inflates that
# to ~43 MB. 64 MB leaves headroom for JSON framing without letting a local
# page push the process into unbounded memory growth.
MAX_REQUEST_BYTES = 64 * 1024 * 1024
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


class WebUIServer(RoutesMixin):
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

    def _log_exchange(self, user_content, assistant_text: str,
                      snapshot: bool = True) -> None:
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
                # A reply that ran no tools cannot have changed the workspace:
                # skipping its snapshot avoids a needless `git add -A` scan
                # without altering rollback semantics (HEAD is unchanged).
                mgr.log_turn({"role": "assistant", "content": assistant_text},
                             snapshot=snapshot)
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
        # base64 decoding + disk writes are blocking: keep them off the loop.
        user_content, attach_info = await asyncio.to_thread(
            self._user_content, message, attachments)
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
            # Still off the loop: snapshotting shells out to git.
            await asyncio.to_thread(
                self._log_exchange, user_content, response.content,
                bool(self._tool_buffer))
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

    async def _reject_too_large(self, writer: asyncio.StreamWriter) -> None:
        """Answer 413 instead of buffering an unbounded request body."""
        limit_mb = MAX_REQUEST_BYTES // (1024 * 1024)
        payload = json.dumps(
            {"error": f"请求体过大，上限 {limit_mb} MB"},
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            writer.write(
                "HTTP/1.1 413 Payload Too Large\r\n"
                "Content-Type: application/json; charset=utf-8\r\n"
                f"Content-Length: {len(payload)}\r\n"
                "Access-Control-Allow-Origin: *\r\n"
                "Connection: close\r\n\r\n".encode("utf-8") + payload
            )
            await writer.drain()
        except Exception as exc:  # client may have already gone away
            logger.debug("413 response not delivered: %s", describe_exc(exc))
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
                if len(request_data) > MAX_REQUEST_BYTES:
                    await self._reject_too_large(writer)
                    return
                if b"\r\n\r\n" in request_data:
                    head, _, _ = request_data.partition(b"\r\n\r\n")
                    clen = 0
                    for line in head.decode("utf-8", "replace").split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            try:
                                clen = int(line.split(":", 1)[1].strip())
                            except ValueError:
                                clen = 0
                    if clen > MAX_REQUEST_BYTES:
                        await self._reject_too_large(writer)
                        return
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
            return await self._route_chat(body)

        elif path == "/api/approve" and method == "POST":
            return self._route_approve(body)

        elif path == "/api/interrupt" and method == "POST":
            return self._route_interrupt()

        elif path == "/api/mode" and method == "POST":
            return self._route_mode(body)

        elif path == "/api/reasoning" and method == "POST":
            return self._route_reasoning(body)

        elif path == "/api/thinking" and method == "POST":
            return self._route_thinking()

        elif path == "/api/cache" and method == "GET":
            return self._route_cache()

        elif path == "/api/status" and method == "GET":
            return self._route_status()

        elif path == "/api/rules" and method == "GET":
            return self._route_rules()

        elif path == "/api/skills" and method == "GET":
            return self._route_skills_get()

        elif path == "/api/skills" and method == "POST":
            return self._route_skills_post(body)

        elif path == "/api/sessions" and method == "GET":
            return self._route_sessions_list()

        elif path == "/api/session/current" and method == "GET":
            return self._route_session_current()

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
            return self._route_sessions_archived()

        elif path == "/api/session/delete" and method == "POST":
            return self._session_action(body, "delete")

        elif path == "/api/session/archive" and method == "POST":
            return self._session_action(body, "archive")

        elif path == "/api/session/unarchive" and method == "POST":
            return self._session_action(body, "unarchive")

        elif path == "/api/session/fork" and method == "POST":
            return self._route_session_fork(body)

        elif path == "/api/session/rename" and method == "POST":
            return self._route_session_rename(body)

        elif path == "/api/workspaces" and method == "GET":
            return self._route_workspaces()

        elif path == "/api/workspace/switch" and method == "POST":
            return self._route_workspace_switch(body)

        elif path == "/api/checkpoints" and method == "GET":
            return self._route_checkpoints()

        elif path == "/api/rewind" and method == "POST":
            return self._route_rewind(body)

        elif path == "/api/rollback" and method == "POST":
            return self._route_rollback(body)

        elif path == "/api/snapshots" and method == "GET":
            return self._route_snapshots()

        elif path == "/api/memory" and method == "GET":
            return self._route_memory()

        elif path == "/api/goal" and method == "POST":
            return self._route_goal(body)

        elif path == "/api/decide" and method == "POST":
            return self._route_decide(body)

        elif path == "/api/oracle" and method == "POST":
            return await self._route_oracle(body)

        elif path == "/api/share" and method == "POST":
            return self._route_share()

        elif path == "/api/inbox" and method == "GET":
            return self._route_inbox()

        elif path == "/api/peers" and method == "GET":
            return self._route_peers()

        elif path == "/api/msg" and method == "POST":
            return self._route_msg(body)

        elif path == "/api/usage" and method == "GET":
            return self._route_usage()

        elif path == "/api/context" and method == "GET":
            return self._route_context()

        elif path == "/api/compress" and method == "GET":
            return self._route_compress()

        elif path == "/api/agents" and method == "GET":
            return self._route_agents()

        elif path == "/api/hello-my-zouwucode" and method == "POST":
            return await self._route_hello(body)

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
        print("  Press Ctrl+C to stop\n")

        async with self._server:
            await self._server.serve_forever()

    async def stop(self):
        """Stop the web server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()