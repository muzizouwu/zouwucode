"""Regression: `zouwucode --tui` inherits the CLI overrides.

Defect pinned: run_tui() built its app straight from config.yaml, so
--model/--api-key/--mode/--reasoning/--compress/--max-tokens/--keep-turns
were silently ignored in TUI mode (they only worked for CLI/Web).
"""

import inspect


class TestTuiOverrides:
    def _tui(self, overrides):
        from zouwucode.tui.textual_app import ZOUWUCODETUI
        return ZOUWUCODETUI(overrides=overrides)

    def test_mode_and_reasoning_reach_engine(self):
        app = self._tui({"mode": "plan", "reasoning": "low"})
        assert app.config.sandbox.default_mode == "plan"
        assert app.config.reasoning_intensity == "low"
        assert app.current_mode == "plan"
        assert app.engine._mode == "plan"

    def test_model_and_key_reach_the_provider_config(self):
        app = self._tui({"model": "test-model-x", "api_key": "sk-test-y"})
        prov = app.config.providers[app.config.default_provider]
        assert prov.model == "test-model-x"
        assert prov.api_key == "sk-test-y"

    def test_compression_flags_reach_engine_config(self):
        app = self._tui({"compress": "balanced", "max_tokens": 12345,
                         "keep_turns": 7})
        assert app.config.context.enabled is True
        assert app.config.context.level == "balanced"
        assert app.config.context.max_context_tokens == 12345
        assert app.config.context.keep_last_n == 7
        assert app.compressor_level == "balanced"

    def test_compress_off_disables(self):
        app = self._tui({"compress": "off"})
        assert app.config.context.enabled is False
        assert app.compressor_level == "off"

    def test_no_overrides_keeps_defaults(self):
        app = self._tui(None)
        assert app.config.context.enabled is False
        assert app.compressor_level == "balanced"

    def test_run_tui_accepts_overrides(self):
        from zouwucode.tui.textual_app import run_tui
        assert "overrides" in inspect.signature(run_tui).parameters

    def test_main_passes_overrides_to_tui(self):
        from zouwucode import __main__
        src = inspect.getsource(__main__)
        assert "overrides={" in src
