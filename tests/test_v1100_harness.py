"""v1.10.0 — DeepSeek Harness (dsh) integration.

Pinned here:

1. The adapter must be OPTIONAL: importing zouwucode never requires
   ``deepseek-harness-sdk``, and a missing SDK produces an install hint
   instead of a traceback (the SDK is a developer preview and will move).
2. Config → SDK mapping must be explicit: provider route, model, reasoning
   effort, DSH home and timeouts are all resolved deterministically, with CLI
   overrides winning and unsupported combinations rejected early.
3. Result mapping must be honest: ``finish_reason == "error"`` is a failure,
   not a successful empty answer.
"""

import json
import sys
import types

import pytest

from zouwucode.config import HarnessConfig, ProviderConfig, ZOUWUCODEConfig
from zouwucode.harness import deepseek as ds
from zouwucode.harness import cli as cli_mod
from zouwucode.harness.cli import harness_main


def _config(tmp_path, api_type="deepseek", model="deepseek-flash"):
    cfg = ZOUWUCODEConfig()
    cfg.data_dir = str(tmp_path)
    cfg.providers = {
        "deepseek": ProviderConfig(
            api_key="test-key", base_url="https://api.deepseek.com",
            model=model, api_type=api_type),
    }
    cfg.default_provider = "deepseek"
    cfg.reasoning_intensity = "max"
    return cfg


# ── fake SDK ────────────────────────────────────────────────────────────────

class _FakeResult:
    def __init__(self, text, reason="completed", events=(1, 2), notifications=()):
        self.session_id = "fake-session"
        self.final_response = text
        self.finish_reason = reason
        self.events = list(events)
        self.notifications = list(notifications)


class _FakeHarness:
    """Stands in for ``deepseek_harness.DeepSeekHarness``."""

    captured: dict = {}
    next_reason = "completed"
    next_events = None
    closed = False

    def __init__(self, **kwargs):
        _FakeHarness.captured = kwargs
        _FakeHarness.closed = False

    def run(self, prompt, session_id=None, on_notification=None):
        if on_notification is not None:
            on_notification("subagent-start")
        events = (_FakeHarness.next_events
                  if _FakeHarness.next_events is not None
                  else [{"type": "turn/start"}, {"type": "turn/end"}])
        return _FakeResult(f"done: {prompt}", reason=_FakeHarness.next_reason,
                           events=events)

    def close(self):
        _FakeHarness.closed = True


@pytest.fixture
def fake_sdk(monkeypatch):
    module = types.ModuleType("deepseek_harness")
    module.DeepSeekHarness = _FakeHarness
    monkeypatch.setitem(sys.modules, "deepseek_harness", module)
    # The CLI binds the probe at import time, so patch its reference too —
    # otherwise the "SDK present" cases silently exercise the missing path.
    monkeypatch.setattr(ds, "sdk_installed", lambda: True)
    monkeypatch.setattr(cli_mod, "sdk_installed", lambda: True)
    _FakeHarness.next_reason = "completed"
    _FakeHarness.next_events = None
    yield module
    for key in list(sys.modules):
        if key == "deepseek_harness":
            sys.modules.pop(key, None)


@pytest.fixture
def no_sdk(monkeypatch):
    monkeypatch.setattr(ds, "sdk_installed", lambda: False)
    monkeypatch.setattr(cli_mod, "sdk_installed", lambda: False)


@pytest.fixture
def cfg_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "providers:\n"
        "  deepseek:\n"
        "    api_key: test-key\n"
        "    base_url: https://api.deepseek.com\n"
        "    model: deepseek-flash\n"
        "    api_type: deepseek\n"
        f"data_dir: {tmp_path.as_posix()}\n"
        "reasoning_intensity: max\n",
        encoding="utf-8")
    return path


# ── config model ────────────────────────────────────────────────────────────

class TestHarnessConfig:
    def test_off_by_default(self):
        cfg = HarnessConfig()
        assert cfg.enabled is False
        assert cfg.profile == "sdk"
        assert cfg.max_tokens == 49152
        assert cfg.patches == []

    def test_root_config_exposes_it(self):
        assert ZOUWUCODEConfig().harness.profile == "sdk"


# ── settings resolution ─────────────────────────────────────────────────────

class TestResolveSettings:
    def test_maps_deepseek_api_type_to_dsh_route(self, tmp_path):
        s = ds.resolve_settings(_config(tmp_path), cwd=tmp_path)
        assert s.provider == "deepseek-official"
        assert s.model == "deepseek-flash"
        assert s.api_key == "test-key"
        # providers.<key>.base_url is OpenAI-compatible and must NOT be
        # forwarded: dsh's own provider uses a different API shape (doing so
        # produced HTTP 404 on the first turn).
        assert s.base_url == ""

    def test_explicit_base_url_is_forwarded(self, tmp_path):
        s = ds.resolve_settings(_config(tmp_path), cwd=tmp_path,
                                base_url="https://example.test/override")
        assert s.base_url == "https://example.test/override"

    def test_reasoning_intensity_is_not_inherited(self, tmp_path):
        """dsh rejects effort ids it does not know (deepseek-official refuses
        "medium"), so zouwucode's tiers must not leak into the SDK by default."""
        cfg = _config(tmp_path)
        assert cfg.reasoning_intensity == "max"
        assert ds.resolve_settings(cfg, cwd=tmp_path).reasoning_effort == ""

    def test_configured_effort_is_forwarded(self, tmp_path):
        cfg = _config(tmp_path)
        cfg.harness.reasoning_effort = "max"
        assert ds.resolve_settings(cfg, cwd=tmp_path).reasoning_effort == "max"

    def test_dsh_home_defaults_under_data_dir(self, tmp_path):
        s = ds.resolve_settings(_config(tmp_path), cwd=tmp_path)
        assert s.dsh_home == (tmp_path / "dsh_home").resolve()

    def test_explicit_overrides_win(self, tmp_path):
        cfg = _config(tmp_path)
        cfg.harness.provider = "deepseek-official"
        cfg.harness.model = "from-config"
        s = ds.resolve_settings(
            cfg, cwd=tmp_path, dsh_home=tmp_path / "custom",
            profile="sdk-minimal", model="from-cli",
            reasoning_effort="low", max_tokens=1234,
            request_timeout_seconds=7.0, patches=("a.yml",))
        assert s.dsh_home == (tmp_path / "custom").resolve()
        assert s.profile == "sdk-minimal"
        assert s.model == "from-cli"
        assert s.reasoning_effort == "low"
        assert s.max_tokens == 1234
        assert s.request_timeout_seconds == 7.0
        assert s.patches == ("a.yml",)

    def test_openai_provider_needs_explicit_route(self, tmp_path):
        with pytest.raises(ValueError) as info:
            ds.resolve_settings(
                _config(tmp_path, api_type="openai"), cwd=tmp_path)
        assert "--provider" in str(info.value)

    def test_sdk_kwargs_are_sdk_shaped(self, tmp_path):
        kwargs = ds.resolve_settings(_config(tmp_path), cwd=tmp_path).sdk_kwargs()
        assert kwargs["provider"] == "deepseek-official"
        assert kwargs["model"] == "deepseek-flash"
        assert kwargs["max_tokens"] == 49152
        assert kwargs["initialize_timeout_seconds"] == 30.0
        assert kwargs["dsh_home"].endswith("dsh_home")
        assert "request_timeout_seconds" not in kwargs  # 0 = unbounded
        # Only forwarded when explicitly configured (see effort test above).
        assert "reasoning_effort" not in kwargs
        assert "base_url" not in kwargs


# ── runner ──────────────────────────────────────────────────────────────────

class TestHarnessRunner:
    def test_missing_sdk_is_actionable(self, tmp_path, no_sdk):
        runner = ds.HarnessRunner(ds.resolve_settings(_config(tmp_path), cwd=tmp_path))
        with pytest.raises(ds.HarnessUnavailable) as info:
            runner.run("hi")
        assert "pip install deepseek-harness-sdk" in str(info.value)

    def test_runs_maps_result_and_closes(self, tmp_path, fake_sdk):
        runner = ds.HarnessRunner(ds.resolve_settings(_config(tmp_path), cwd=tmp_path))
        result = runner.run("任务", session_id="s-1", on_notification=lambda _e: None)
        assert result.text == "done: 任务"
        assert result.finish_reason == "completed"
        assert result.event_count == 2
        assert _FakeHarness.captured["provider"] == "deepseek-official"
        assert _FakeHarness.captured["api_key"] == "test-key"
        runner.close()
        assert _FakeHarness.closed is True

    def test_error_finish_reason_raises(self, tmp_path, fake_sdk):
        _FakeHarness.next_reason = "error"
        runner = ds.HarnessRunner(ds.resolve_settings(_config(tmp_path), cwd=tmp_path))
        with pytest.raises(ds.HarnessError):
            runner.run("boom")

    def test_empty_prompt_rejected(self, tmp_path, fake_sdk):
        runner = ds.HarnessRunner(ds.resolve_settings(_config(tmp_path), cwd=tmp_path))
        with pytest.raises(ValueError):
            runner.run("   ")


class TestFailureDiagnostics:
    """A preview runtime that fails must say WHY — bare "error" is useless."""

    def test_turn_end_reason_is_parsed(self):
        events = [
            {"type": "turn/start", "data": {"turn": 1}},
            {"type": "turn/end", "data": {"reason": {
                "kind": "error", "message": "provider rejected the request"}}},
        ]
        assert ds._turn_end_reason(events) == "error — provider rejected the request"

    def test_reason_missing_yields_empty(self):
        assert ds._turn_end_reason([{"type": "turn/start"}]) == ""
        assert ds._turn_end_reason([]) == ""
        assert ds._turn_end_reason(None) == ""

    def test_runner_surfaces_turn_end_reason(self, tmp_path, fake_sdk):
        _FakeHarness.next_reason = "error"
        _FakeHarness.next_events = [
            {"type": "turn/end", "data": {"reason": {
                "kind": "provider-error", "message": "401 invalid api key"}}},
        ]
        runner = ds.HarnessRunner(ds.resolve_settings(_config(tmp_path), cwd=tmp_path))
        with pytest.raises(ds.HarnessError) as info:
            runner.run("x")
        assert "401 invalid api key" in str(info.value)
        assert "--verbose" in str(info.value)

    def test_known_upstream_defects_get_a_hint(self):
        """Windows koffi defect and unsupported effort ids must not look like
        user errors — each maps to a concrete next step."""
        koffi = ds._platform_hint(
            "{'message': 'The specified module could not be found. "
            "@koromix/koffi-win32-x64/koffi.node'}")
        assert "--profile sdk-minimal" in koffi

        effort = ds._platform_hint(
            'model "deepseek-v4-flash" does not support reasoning effort "medium"')
        assert "--reasoning" in effort

        assert ds._platform_hint("something else") == ""


class TestNotificationFormatting:
    def test_structured_payload_keeps_reason_fields(self):
        class _Notification:
            method = "session.event"
            payload = {"event": {"type": "turn/end", "data": {
                "reason": {"kind": "error", "message": "boom"}}}}

        text = cli_mod._format_notification(_Notification())
        assert text.startswith("session.event: ")
        assert '"kind": "error"' in text

    def test_plain_text_is_bounded(self):
        assert len(cli_mod._format_notification("x" * 2000)) <= 600


# ── CLI ─────────────────────────────────────────────────────────────────────

class TestHarnessCLI:
    def test_doctor_ok_with_sdk(self, cfg_file, fake_sdk, capsys):
        assert harness_main(["--doctor", "--config", str(cfg_file)]) == 0
        out = capsys.readouterr().out
        assert "installed" in out
        assert "deepseek-official" in out

    def test_doctor_without_sdk_exits_2(self, cfg_file, no_sdk, capsys):
        assert harness_main(["--doctor", "--config", str(cfg_file)]) == 2
        assert "deepseek-harness-sdk" in capsys.readouterr().out

    def test_prints_final_text(self, cfg_file, fake_sdk, capsys):
        assert harness_main(["修好测试", "--config", str(cfg_file)]) == 0
        assert "done: 修好测试" in capsys.readouterr().out

    def test_json_output_shape(self, cfg_file, fake_sdk, capsys):
        assert harness_main(
            ["解释结构", "--json", "--config", str(cfg_file)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["finish_reason"] == "completed"
        assert payload["text"] == "done: 解释结构"
        assert set(payload) >= {"session_id", "events", "notifications"}

    def test_missing_sdk_exits_2_with_hint(self, cfg_file, no_sdk, capsys):
        assert harness_main(["hi", "--config", str(cfg_file)]) == 2
        assert "pip install deepseek-harness-sdk" in capsys.readouterr().err

    def test_missing_prompt_exits_2(self, cfg_file, fake_sdk, monkeypatch, capsys):
        class _Tty:
            def isatty(self):
                return True

        monkeypatch.setattr(sys, "stdin", _Tty())
        assert harness_main(["--config", str(cfg_file)]) == 2
        assert "缺少任务内容" in capsys.readouterr().err

    def test_verbose_streams_notifications(self, cfg_file, fake_sdk, capsys):
        assert harness_main(
            ["hi", "--verbose", "--config", str(cfg_file)]) == 0
        captured = capsys.readouterr()
        assert "subagent-start" in captured.err
        assert "finish=completed" in captured.err
