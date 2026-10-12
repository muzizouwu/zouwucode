"""Regression: hello-my-zouwucode config knobs are actually read.

Defects pinned:
- default_category was declared but unused: the orchestrator's worker
  fallback and Atlas's per-todo fallback both hardcoded "deep".
- max_review_rounds was never read: _run_plan hardcoded
  high_accuracy=False and never passed the rounds, so the Momus review
  loop could never run even when configured.
"""

import inspect

from zouwucode.config import ZOUWUCODEConfig


class TestDefaultCategoryWiring:
    def test_orchestrator_reads_configured_default_category(self, tmp_path):
        from hello_my_zouwucode.orchestrator import SisyphusOrchestrator
        cfg = ZOUWUCODEConfig()
        cfg.hello_my_zouwucode.default_category = "ultrabrain"
        orch = SisyphusOrchestrator(cfg, engine=None, omo_dir=tmp_path)
        assert orch.default_category == "ultrabrain"
        assert orch.atlas.default_category == "ultrabrain"

    def test_unknown_config_falls_back_to_deep(self, tmp_path):
        from hello_my_zouwucode.orchestrator import SisyphusOrchestrator
        orch = SisyphusOrchestrator(ZOUWUCODEConfig(), engine=None,
                                    omo_dir=tmp_path)
        assert orch.default_category == "deep"

    def test_atlas_uses_default_for_categoryless_todos(self, tmp_path):
        from hello_my_zouwucode.atlas import Atlas
        from hello_my_zouwucode.boulder import BoulderState
        boulder = BoulderState(tmp_path)
        atlas = Atlas(None, boulder, tmp_path, default_category="writing")
        assert atlas.default_category == "writing"
        src = inspect.getsource(Atlas.execute)
        assert "self.default_category" in src

    def test_hephaestus_fallback_uses_default_category(self, tmp_path):
        from hello_my_zouwucode.orchestrator import SisyphusOrchestrator
        src = inspect.getsource(SisyphusOrchestrator._handle_implementation)
        assert "self.default_category" in src


class TestReviewRoundsWiring:
    def test_run_plan_accepts_and_threads_review_rounds(self):
        from hello_my_zouwucode.orchestrator import SisyphusOrchestrator
        sig = inspect.signature(SisyphusOrchestrator.run_plan)
        assert "max_review_rounds" in sig.parameters
        src = inspect.getsource(SisyphusOrchestrator.run_plan)
        assert "max_review_rounds=max_review_rounds" in src

    def test_planner_still_accepts_review_rounds(self):
        from hello_my_zouwucode.planner import Planner
        sig = inspect.signature(Planner.create_plan)
        assert "max_review_rounds" in sig.parameters

    def test_module_plan_uses_config_value(self):
        from hello_my_zouwucode.module import HelloMyZouwucodeModule
        src = inspect.getsource(HelloMyZouwucodeModule._run_plan)
        assert "max_review_rounds" in src
        assert "high_accuracy=review_rounds > 0" in src
