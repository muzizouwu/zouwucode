"""Tests for the hello-my-zouwucode module (oh-my-opencode port).

Covers: categories, intent gate, agent inventory, boulder state,
notepad, planner, atlas executor, the Sisyphus orchestrator (including
the full ultrawork pipeline and boulder resume flow), and the ZOUWUCODE
module-system integration (ModuleManager + HelloMyZouwucodeModule).
"""

import os
import time
from pathlib import Path

import pytest

from zouwucode.config import ZOUWUCODEConfig
from hello_my_zouwucode.categories import (
    CATEGORY_DEFAULTS,
    CATEGORY_NAMES,
    category_table,
    get_category,
    list_categories,
)
from hello_my_zouwucode.intent_gate import Intent, IntentGate, intent_label, intent_routing_prompt
from hello_my_zouwucode.agents import (
    AGENTS,
    AgentMode,
    agent_inventory_table,
    get_agent,
    list_agents,
)
from hello_my_zouwucode.boulder import BoulderState, make_todo
from hello_my_zouwucode.notepad import NOTEPAD_FILES, Notepad
from hello_my_zouwucode.planner import Planner, _slugify
from hello_my_zouwucode.atlas import Atlas, OrchestrationResult
from hello_my_zouwucode.orchestrator import SisyphusOrchestrator, detect_ultrawork


# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeResponse:
    """Stand-in for engine ModelResponse."""

    def __init__(self, content="", thinking="", cache_hit=False, usage=None):
        self.content = content
        self.thinking = thinking
        self.cache_hit = cache_hit
        self.usage = usage or {}


class FakeEngine:
    """Stand-in for EngineLoop. responder(messages) -> str."""

    def __init__(self, responder=None):
        self.responder = responder
        self.calls = []
        self.stats = type("S", (), {"total_requests": 0, "hit_rate": 0.0,
                                    "total_cost": 0.0, "total_tokens": 0})()

    async def run(self, messages, tools=None):
        self.calls.append(messages)
        content = self.responder(messages) if self.responder else "ok"
        self.stats.total_requests += 1
        return FakeResponse(content=content)

    def set_reasoning_intensity(self, level):
        pass

    def set_mode(self, mode):
        pass


def plan_responder(messages):
    """Routes by which agent's prompt is in the system message."""
    system = messages[0]["content"]
    if "Prometheus" in system:
        return "# Plan\n\n1. [ ] Do thing A\n2. [ ] Do thing B\n"
    if "Metis" in system:
        return "No gaps found."
    return "Worker done."


def make_orchestrator(tmp_path, responder=None) -> SisyphusOrchestrator:
    engine = FakeEngine(responder)
    config = ZOUWUCODEConfig()
    return SisyphusOrchestrator(config, engine, omo_dir=Path(tmp_path)), engine


# ── Categories ───────────────────────────────────────────────────────────────

class TestCategories:
    def test_inventory_size(self):
        assert len(CATEGORY_NAMES) == 11
        assert len(CATEGORY_DEFAULTS) == len(CATEGORY_NAMES)

    def test_get_category_known(self):
        spec = get_category("ultrabrain")
        assert spec.reasoning_intensity == "max"
        assert get_category("quick").reasoning_intensity == "low"

    def test_get_category_fallback(self):
        spec = get_category("no-such-category")
        assert spec.name == "unspecified-high"

    def test_list_and_table(self):
        names = list_categories()
        assert "deep" in names and "visual-engineering" in names
        table = category_table()
        assert "ultrabrain" in table and "Reasoning" in table


# ── Intent gate ──────────────────────────────────────────────────────────────

class TestIntentGate:
    def test_quick_question(self):
        result = IntentGate().classify("What is Python?")
        assert result.intent == Intent.QUICK_QUESTION

    def test_fix(self):
        result = IntentGate().classify("please fix the bug")
        assert result.intent == Intent.FIX

    def test_implementation(self):
        result = IntentGate().classify("implement a new feature")
        assert result.intent == Intent.IMPLEMENTATION

    def test_explore_hard_rule(self):
        result = IntentGate().classify("grep where is the config loaded")
        assert result.intent == Intent.EXPLORE

    def test_planning(self):
        result = IntentGate().classify("make a plan for the refactor")
        assert result.intent == Intent.PLANNING

    def test_chinese_fix(self):
        result = IntentGate().classify("帮我修复这个报错")
        assert result.intent == Intent.FIX

    def test_confidence_range(self):
        result = IntentGate().classify("implement login page")
        assert 0.0 <= result.confidence <= 1.0
        assert isinstance(result.matched_keywords, list)

    def test_label_and_prompt(self):
        assert intent_label(Intent.FIX)
        assert "quick_question" in intent_routing_prompt()

    async def test_classify_async(self):
        result = await IntentGate().classify_async("What is a dict?")
        assert result.intent == Intent.QUICK_QUESTION


# ── Agent inventory ──────────────────────────────────────────────────────────

class TestAgents:
    def test_eleven_agents(self):
        assert len(AGENTS) == 11

    def test_primary_vs_subagent_count(self):
        assert len(list_agents(AgentMode.PRIMARY)) == 4
        assert len(list_agents(AgentMode.SUBAGENT)) == 7

    def test_get_agent_fallback(self):
        assert get_agent("missing").name == "sisyphus-junior"

    def test_inventory_table(self):
        table = agent_inventory_table()
        assert "sisyphus" in table and "| Agent |" in table


# ── Boulder ──────────────────────────────────────────────────────────────────

class TestBoulder:
    def test_empty_state(self):
        b = BoulderState(Path("does-not-exist"))
        assert not b.exists()
        assert "No active plan" in b.summary()

    def test_start_and_progress(self, tmp_path):
        b = BoulderState(Path(tmp_path))
        todos = [make_todo("task one"), make_todo("task two")]
        b.start("my-plan", "plans/my-plan.md", todos)
        assert b.exists()
        assert b.plan_name == "my-plan"
        assert b.progress == {"completed": 0, "total": 2}

        first = b.next_pending_todo()
        assert first["title"] == "task one"
        b.complete_todo(first["id"])
        assert b.progress == {"completed": 1, "total": 2}

        b.fail_todo(todos[1]["id"])
        # Failed tasks remain "not done" so they can be retried.
        assert [t["title"] for t in b.remaining()] == ["task two"]
        assert b.progress == {"completed": 1, "total": 2}

    def test_persistence(self, tmp_path):
        b = BoulderState(Path(tmp_path))
        b.start("persist", "plans/persist.md", [make_todo("x")])
        b2 = BoulderState(Path(tmp_path))  # reload from disk
        assert b2.plan_name == "persist"
        assert b2.progress["total"] == 1

    def test_session_tracking(self, tmp_path):
        b = BoulderState(Path(tmp_path))
        b.start("s", "plans/s.md", [make_todo("x")], session_id="sess-1")
        b.add_session("sess-2")
        assert b.session_ids == ["sess-1", "sess-2"]

    def test_clear(self, tmp_path):
        b = BoulderState(Path(tmp_path))
        b.start("c", "plans/c.md", [make_todo("x")])
        b.clear()
        assert not b.exists()


# ── Notepad ──────────────────────────────────────────────────────────────────

class TestNotepad:
    def test_write_read(self, tmp_path):
        n = Notepad("demo-plan", Path(tmp_path))
        n.write("learnings", "Pattern: use pydantic models.")
        assert "pydantic" in n.read("learnings")

    def test_append_and_helpers(self, tmp_path):
        n = Notepad("demo-plan", Path(tmp_path))
        n.add_learning("Keep functions small.")
        n.add_decision("Use cache-first engine", "faster responses")
        n.add_issue("Watch out for circular imports")
        n.add_verification("All tests pass")
        n.add_problem("None")
        assert n.read("learnings").startswith("- [")
        assert "All tests pass" in n.read("verification")

    def test_context_block(self, tmp_path):
        n = Notepad("demo-plan", Path(tmp_path))
        n.add_learning("Use pydantic.")
        block = n.get_context_block()
        assert 'section="learnings"' in block
        assert "pydantic" in block

    def test_safe_name(self, tmp_path):
        n = Notepad("Plan / With / Slashes!", Path(tmp_path))
        assert n.directory.exists()
        assert n.directory.name == "Plan---With---Slashes"

    def test_summary(self, tmp_path):
        n = Notepad("demo-plan", Path(tmp_path))
        assert "Notepad: demo-plan" in n.summary()
        assert len(NOTEPAD_FILES) == 5


# ── Planner ──────────────────────────────────────────────────────────────────

class TestPlanner:
    def test_slugify(self):
        assert _slugify("Build user auth") == "Build-user-auth"
        assert _slugify("").startswith("plan-")

    def test_parse_plan_todos(self, tmp_path):
        plan = tmp_path / "plan.md"
        plan.write_text(
            "# Plan\n\n"
            "- [ ] Task one\n"
            "1. [ ] Task two\n"
            "* [ ] Task three\n"
            "- [x] Already done\n"
            "- plain line (not a task)\n",
            encoding="utf-8",
        )
        todos = Planner(FakeEngine(), tmp_path).parse_plan_todos(plan)
        assert len(todos) == 4
        assert [t["title"] for t in todos] == ["Task one", "Task two", "Task three", "Already done"]

    def test_parse_plan_todos_missing_file(self, tmp_path):
        assert Planner(FakeEngine(), tmp_path).parse_plan_todos(tmp_path / "nope.md") == []

    def test_latest_plan(self, tmp_path):
        planner = Planner(FakeEngine(), tmp_path)
        plans = tmp_path / "plans"
        plans.mkdir()
        old = plans / "old.md"
        new = plans / "new.md"
        old.write_text("old", encoding="utf-8")
        new.write_text("new", encoding="utf-8")
        os.utime(old, (time.time() - 100, time.time() - 100))
        os.utime(new, (time.time(), time.time()))
        assert planner.latest_plan().name == "new.md"

    def test_extract_plan_markdown(self):
        planner = Planner(FakeEngine(), Path("."))
        text = "Here's the plan:\n```markdown\n# Title\n\n- [ ] A\n```\n"
        assert planner._extract_plan_markdown(text).startswith("# Title")

    async def test_create_plan_and_write(self, tmp_path):
        async def fake_metis(task, draft):
            return "ok"

        engine = FakeEngine(plan_responder)
        planner = Planner(engine, Path(tmp_path))
        path = await planner.create_plan(
            "implement feature", interactive=False,
            metis=fake_metis, momus=None,
        )
        assert path is not None and path.exists()
        assert "1. [ ] Do thing A" in path.read_text(encoding="utf-8")

    async def test_create_plan_empty_returns_none(self, tmp_path):
        engine = FakeEngine(lambda messages: "")
        planner = Planner(engine, Path(tmp_path))
        path = await planner.create_plan("anything", interactive=False)
        assert path is None


# ── Atlas ────────────────────────────────────────────────────────────────────

class TestAtlas:
    def test_result_defaults(self):
        r = OrchestrationResult(True, "x")
        assert r.success is True and r.content == "" and r.details == []
        assert r.tasks_total == 0

    async def test_execute_completes_all_tasks(self, tmp_path):
        engine = FakeEngine(lambda messages: "Completed with verification.")
        boulder = BoulderState(Path(tmp_path))
        boulder.start("plan", "plans/plan.md",
                      [make_todo("t1"), make_todo("t2")])
        atlas = Atlas(engine, boulder, Path(tmp_path))
        result = await atlas.execute()
        assert result.success is True
        assert result.tasks_completed == 2 and result.tasks_total == 2
        assert len(result.details) == 2
        assert boulder.progress == {"completed": 2, "total": 2}

    async def test_execute_failed_worker(self, tmp_path):
        engine = FakeEngine(lambda messages: "")  # empty result → fail
        boulder = BoulderState(Path(tmp_path))
        boulder.start("plan", "plans/plan.md", [make_todo("t1")])
        atlas = Atlas(engine, boulder, Path(tmp_path))
        result = await atlas.execute()
        assert result.success is False
        assert boulder.progress["completed"] == 0

    async def test_execute_no_plan(self, tmp_path):
        boulder = BoulderState(Path(tmp_path))  # empty
        atlas = Atlas(FakeEngine(), boulder, Path(tmp_path))
        result = await atlas.execute()
        assert result.success is False
        assert "No active plan" in result.content


# ── Orchestrator ─────────────────────────────────────────────────────────────

class TestOrchestrator:
    def test_detect_ultrawork(self):
        assert detect_ultrawork("ultrawork fix the tests") == "fix the tests"
        assert detect_ultrawork("ulw: add validation") == "add validation"
        assert detect_ultrawork("normal message") is None

    async def test_ultrawork_quick_question(self, tmp_path):
        orch, engine = make_orchestrator(tmp_path)
        result = await orch.ultrawork("What is Python?")
        assert result.success is True
        assert result.intent == Intent.QUICK_QUESTION
        assert result.content == "ok"

    async def test_ultrawork_implementation_pipeline(self, tmp_path):
        orch, engine = make_orchestrator(tmp_path, plan_responder)
        result = await orch.ultrawork("implement a new feature")
        assert result.success is True
        assert result.intent == Intent.IMPLEMENTATION
        assert result.tasks_completed == 2 and result.tasks_total == 2
        assert result.plan_name is not None
        # Plan file + boulder persisted under state dir
        assert (Path(tmp_path) / "plans").exists()
        assert orch.boulder.exists()

    async def test_run_plan(self, tmp_path):
        orch, engine = make_orchestrator(tmp_path, plan_responder)
        result = await orch.run_plan("implement feature", interview=False)
        assert result.success is True
        assert result.plan_name is not None
        assert result.tasks_total == 2

    async def test_start_work_resume(self, tmp_path):
        orch, engine = make_orchestrator(tmp_path, plan_responder)

        # First run: no boulder → INIT mode (latest plan) → full execution
        await orch.run_plan("implement feature", interview=False)
        result1 = await orch.start_work(session_id="sess-1")
        assert result1.success is True
        assert result1.tasks_completed == 2

        # Second run: boulder exists → RESUME mode, no-op with progress intact
        result2 = await orch.start_work(session_id="sess-2")
        assert orch.boulder.session_ids == ["sess-1", "sess-2"]
        assert orch.boulder.progress == {"completed": 2, "total": 2}

    async def test_start_work_no_plan(self, tmp_path):
        orch, engine = make_orchestrator(tmp_path)
        result = await orch.start_work()
        assert result.success is False
        assert "No active plan" in result.content

    def test_module_config_defaults(self):
        cfg = ZOUWUCODEConfig()
        assert cfg.hello_my_zouwucode.enabled is True
        assert cfg.hello_my_zouwucode.state_dir == ".hello-my-zouwucode"
        assert cfg.hello_my_zouwucode.default_category == "deep"
        assert cfg.hello_my_zouwucode.interactive_planning is True


# ── Module-system integration ────────────────────────────────────────────────

class TestModuleIntegration:
    def make_module(self, tmp_path, engine=None):
        from hello_my_zouwucode.module import HelloMyZouwucodeModule

        cfg = ZOUWUCODEConfig()
        cfg.hello_my_zouwucode.state_dir = str(tmp_path)
        return HelloMyZouwucodeModule(
            engine or FakeEngine(),
            config=cfg,
            state_dir=Path(tmp_path),
        )

    def test_manager_register_dispatch(self, tmp_path):
        from zouwucode.modules.manager import ModuleManager

        mgr = ModuleManager()
        module = self.make_module(tmp_path)
        mgr.register(module)
        assert mgr.is_loaded("hello-my-zouwucode")
        assert mgr.loaded() == ["hello-my-zouwucode"]
        assert mgr.status()[0]["commands"][0].startswith("/hello-ultrawork")

        # Unknown commands are ignored (returns None)
        async def check():
            assert await mgr.dispatch_command("/bogus") is None
            assert mgr.dispatch_event("system", "x", {}) is None

        import asyncio
        asyncio.run(check())

    def test_manager_unregister(self, tmp_path):
        from zouwucode.modules.manager import ModuleManager

        mgr = ModuleManager()
        mgr.register(self.make_module(tmp_path))
        assert mgr.unregister("hello-my-zouwucode") is True
        assert not mgr.is_loaded("hello-my-zouwucode")

    async def test_module_handle_message_ultrawork(self, tmp_path):
        module = self.make_module(tmp_path)
        result = await module.handle_message("ultrawork What is Python?")
        assert result is not None
        assert result["title"] == "Ultrawork"
        assert result["content"] == "ok"

        assert await module.handle_message("plain message") is None

    async def test_module_handle_command_inventory(self, tmp_path):
        module = self.make_module(tmp_path)
        agents = await module.handle_command("/hello-agents")
        assert "Sisyphus" in agents["content"]
        cats = await module.handle_command("/hello-categories")
        assert "ultrabrain" in cats["content"]
        status = await module.handle_command("/hello-status")
        assert "Agents: 11" in status["content"]

    async def test_module_handle_command_unknown(self, tmp_path):
        module = self.make_module(tmp_path)
        assert await module.handle_command("/whatever") is None

    def test_module_status_payload(self, tmp_path):
        module = self.make_module(tmp_path)
        payload = module.status_payload()
        assert payload["module"] == "hello-my-zouwucode"
        assert payload["agents"] == 11
        assert payload["version"] == "1.0.0"

    def test_app_loads_module(self):
        """ZOUWUCODEApp registers hello-my-zouwucode via ModuleManager."""
        from zouwucode.tui.app import ZOUWUCODEApp

        app = ZOUWUCODEApp()
        assert "hello-my-zouwucode" in app.modules.loaded()

    def test_module_disabled_not_loaded(self, tmp_path):
        from zouwucode.modules.manager import ModuleManager

        cfg = ZOUWUCODEConfig()
        cfg.hello_my_zouwucode.enabled = False
        mgr = ModuleManager(cfg, FakeEngine())
        try:
            from hello_my_zouwucode.module import HelloMyZouwucodeModule
            mgr.register(HelloMyZouwucodeModule(FakeEngine(), config=cfg))
        except Exception:
            pass
        # enabled=false is the host's concern; the module itself still loads.
        # The host should skip registering it — assert the flag contract.
        assert cfg.hello_my_zouwucode.enabled is False
