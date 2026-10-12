"""Tests for the config module."""

import pytest
import tempfile
from pathlib import Path

from zouwucode.config import ZOUWUCODEConfig, ProviderConfig, CacheConfig, SandboxConfig


class TestZOUWUCODEConfig:
    """Tests for the ZOUWUCODEConfig class."""

    def test_default_config(self):
        config = ZOUWUCODEConfig()
        assert config.default_provider == "deepseek"
        assert config.cache.max_prefix_tokens == 128_000
        assert config.sandbox.default_mode == "agent"
        assert config.session.save_enabled is True

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"

            # Create and save
            config = ZOUWUCODEConfig()
            config.default_provider = "openai"
            config.log_level = "debug"
            config.save(config_path)

            # Load and verify
            loaded = ZOUWUCODEConfig.load(config_path)
            assert loaded.default_provider == "openai"
            assert loaded.log_level == "debug"

    def test_provider_config(self):
        config = ZOUWUCODEConfig()
        config.providers["deepseek"] = ProviderConfig(
            api_key="sk-test",
            model="deepseek-v4-flash",
            api_type="deepseek",
        )
        assert config.providers["deepseek"].api_key == "sk-test"
        assert config.providers["deepseek"].model == "deepseek-v4-flash"

    def test_cache_config(self):
        config = CacheConfig()
        assert config.max_prefix_tokens == 128_000

    def test_sandbox_config(self):
        config = SandboxConfig()
        assert config.default_mode == "agent"
        assert config.allow_shell is True
        assert config.allow_network is True


class TestDeadKnobsRemoved:
    """Declared-but-never-read knobs were deleted in 1.0.3: a knob that
    documents behavior which does not exist is worse than no knob."""

    def test_removed_fields_are_really_gone(self):
        cfg = ZOUWUCODEConfig()
        for dead in ("theme", "language"):
            assert not hasattr(cfg, dead), dead
        for dead in ("enabled", "append_only"):
            assert not hasattr(cfg.cache, dead), dead
        for dead in ("auto_save_interval",):
            assert not hasattr(cfg.session, dead), dead

    def test_readded_functional_knobs_are_present(self):
        # 1.0.4 re-added the two knobs that CAN do something real.
        cfg = ZOUWUCODEConfig()
        assert cfg.cache.stats_window == 50
        assert cfg.session.rollback_enabled is True

    def test_config_files_do_not_mention_them(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        for name in ("config.yaml", "config.example.yaml"):
            path = root / name
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for dead in ("append_only", "auto_save_interval"):
                assert dead not in text, f"{dead} still in {name}"
            assert "\ntheme:" not in text, f"theme still in {name}"
            assert "\nlanguage:" not in text, f"language still in {name}"