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
        assert config.cache.enabled is True
        assert config.sandbox.default_mode == "agent"
        assert config.session.save_enabled is True

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"

            # Create and save
            config = ZOUWUCODEConfig()
            config.default_provider = "openai"
            config.theme = "light"
            config.save(config_path)

            # Load and verify
            loaded = ZOUWUCODEConfig.load(config_path)
            assert loaded.default_provider == "openai"
            assert loaded.theme == "light"

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
        assert config.enabled is True
        assert config.max_prefix_tokens == 128_000
        assert config.append_only is True

    def test_sandbox_config(self):
        config = SandboxConfig()
        assert config.default_mode == "agent"
        assert config.allow_shell is True
        assert config.allow_network is True