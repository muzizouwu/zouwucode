"""HTTP route handlers for the local Web UI.

The transport layer (request parsing, response framing, static assets and
the SSE chat stream) stays in ``server.py``; this module holds the per-endpoint
bodies. ``WebUIServer`` inherits ``RoutesMixin``, so every handler keeps
using the same ``self`` state and the HTTP surface is unchanged.
"""

import asyncio
import json
import time
import traceback
from pathlib import Path

from ..errors import describe_exc


class RoutesMixin:
    """Endpoint implementations mixed into ``WebUIServer``."""

    # ── Chat / engine control ─────────────────────────────────────────────

    async def _route_chat(self, body: str) -> str:
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
            user_content, attach_info = await asyncio.to_thread(
                self._user_content, message, attachments)

            messages = []
            if project_ctx:
                messages.append({"role": "system", "content": project_ctx})
            messages.append({"role": "user", "content": user_content})

            response = await self.engine.run(
                messages=messages,
                tools=self.tools.get_schemas(),
            )
            await asyncio.to_thread(
                self._log_exchange, user_content, response.content,
                bool(self._tool_buffer))

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

    def _route_approve(self, body: str) -> str:
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

    def _route_interrupt(self) -> str:
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

    def _route_mode(self, body: str) -> str:
        try:
            data = json.loads(body)
            mode = data.get("mode", "agent")
            self.engine.set_mode(mode)
            return self._json_response({"mode": mode})
        except Exception as e:
            return self._json_response({"error": str(e)}, 400)

    def _route_reasoning(self, body: str) -> str:
        try:
            data = json.loads(body)
            level = data.get("reasoning", "medium")
            self.config.reasoning_intensity = level
            self.engine.set_reasoning_intensity(level)
            return self._json_response({"reasoning": level})
        except Exception as e:
            return self._json_response({"error": str(e)}, 400)

    def _route_thinking(self) -> str:
        # Toggle live thinking display (mirrors /thinking on CLI/TUI)
        self.config.show_thinking = not getattr(self.config, "show_thinking", True)
        return self._json_response({"show_thinking": self.config.show_thinking})

    def _route_cache(self) -> str:
        return self._json_response(self.engine.get_cache_summary())

    def _route_status(self) -> str:
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

    def _route_rules(self) -> str:
        if self._rules:
            return self._json_response({"content": self._rules.load()})
        return self._json_response({"content": ""})

    def _route_skills_get(self) -> str:
        if self._skills:
            return self._json_response({
                "available": self._skills.list_available(),
                "loaded": self._skills.list_loaded(),
            })
        return self._json_response({"available": [], "loaded": []})

    def _route_skills_post(self, body: str) -> str:
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

    # ── Sessions / workspaces / snapshots ─────────────────────────────────

    def _route_sessions_list(self) -> str:
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

    def _route_session_current(self) -> str:
        mgr = getattr(self, "sessions_manager", None)
        sid = getattr(mgr, "current_session_id", None) if mgr else None
        turns = mgr.current_log() if mgr else []
        return self._json_response({
            "session_id": sid,
            "turns": [{"role": t.get("role"), "content": t.get("content", "")}
                      for t in turns if t.get("role") in ("user", "assistant")],
        })

    def _route_sessions_archived(self) -> str:
        mgr = self._sessions()
        return self._json_response({
            "sessions": mgr.list_archived() if mgr is not None else []})

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

    def _route_session_fork(self, body: str) -> str:
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

    def _route_session_rename(self, body: str) -> str:
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

    def _route_workspaces(self) -> str:
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

    def _route_workspace_switch(self, body: str) -> str:
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

    def _route_checkpoints(self) -> str:
        mgr = getattr(self, "sessions_manager", None)
        cps = mgr.list_checkpoints() if mgr and hasattr(mgr, "list_checkpoints") else []
        log = mgr.current_log() if mgr else []
        return self._json_response({"checkpoints": cps, "messages": len(log)})

    def _route_rewind(self, body: str) -> str:
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

    def _route_rollback(self, body: str) -> str:
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

    def _route_snapshots(self) -> str:
        manager = getattr(self, "sessions_manager", None)
        snaps = manager.list_snapshots() if manager is not None else []
        return self._json_response({"snapshots": snaps})

    # ── Project memory / oracle / sharing ─────────────────────────────────

    def _route_memory(self) -> str:
        pm = getattr(self.coordinator, "project_memory", None)
        if pm is None:
            return self._json_response({"context": "", "goal": "", "decisions": []})
        return self._json_response({
            "context": pm.get_full_context(),
            "goal": pm.get_active_goal(),
            "decisions": pm.get_decisions(limit=10),
        })

    def _route_goal(self, body: str) -> str:
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

    def _route_decide(self, body: str) -> str:
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

    async def _route_oracle(self, body: str) -> str:
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

    def _route_share(self) -> str:
        app = getattr(self, "app", None)
        if app is not None and hasattr(app, "share_current_session"):
            return self._json_response(app.share_current_session())
        return self._json_response({"ok": False, "error": "sharing unavailable"}, 400)

    # ── Cross-session messaging / misc ────────────────────────────────────

    def _route_inbox(self) -> str:
        bus = getattr(self, "bus", None)
        if bus is None:
            return self._json_response({"enabled": False, "messages": []})
        return self._json_response({"enabled": True, "messages": bus.drain()})

    def _route_peers(self) -> str:
        bus = getattr(self, "bus", None)
        if bus is None:
            return self._json_response({"enabled": False, "peers": []})
        keys = ("agent", "role", "pid", "last_seen", "unread")
        peers = [{k: p.get(k) for k in keys} for p in bus.peers()]
        return self._json_response({"enabled": True, "peers": peers})

    def _route_msg(self, body: str) -> str:
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

    def _route_usage(self) -> str:
        engine = self.engine
        if hasattr(engine, "get_usage_summary"):
            return self._json_response(engine.get_usage_summary())
        return self._json_response({})

    def _route_context(self) -> str:
        ctx = getattr(self.config, "context", None)
        return self._json_response({
            "prompt_tokens": getattr(getattr(self.engine, "stats", None),
                                     "total_prompt_tokens", 0),
            "max_context_tokens": getattr(ctx, "max_context_tokens", 1048576),
            "enabled": getattr(ctx, "enabled", False),
            "level": getattr(ctx, "level", "off"),
        })

    def _route_compress(self) -> str:
        ctx = getattr(self.config, "context", None)
        return self._json_response({
            "enabled": getattr(ctx, "enabled", False),
            "level": getattr(ctx, "level", "balanced"),
            "max_context_tokens": getattr(ctx, "max_context_tokens", 1048576),
            "keep_last_n": getattr(ctx, "keep_last_n", 50),
        })

    def _route_agents(self) -> str:
        mgr = self.subagent_manager
        agents = []
        if mgr is not None and hasattr(mgr, "status"):
            try:
                agents = mgr.status()
            except Exception:  # noqa: BLE001 — status is best-effort
                agents = []
        return self._json_response({"agents": agents})

    async def _route_hello(self, body: str) -> str:
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
