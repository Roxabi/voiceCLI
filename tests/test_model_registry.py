"""Tests for config loading functions in voicecli.config.

Tests for load_nats_config and load_tts_config default values and clamping.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestConfigLoading:
    """Tests for config loading with defaults and value clamping."""

    def test_load_nats_config_defaults(self, tmp_path, monkeypatch):
        """load_nats_config returns defaults when no config."""
        # Arrange
        from voicecli.config import load_nats_config

        monkeypatch.setattr("voicecli.config._find_config", lambda: None)

        # Act
        result = load_nats_config()

        # Assert
        assert result["max_cached_engines"] == 2

    def test_load_nats_config_clamps_high(self, tmp_path):
        """max_cached_engines clamped to 5 when set higher."""
        # Arrange
        from voicecli.config import load_nats_config

        cfg = tmp_path / "voicecli.toml"
        cfg.write_text("[nats]\nmax_cached_engines = 100\n")

        # Act
        result = load_nats_config(cfg)

        # Assert
        assert result["max_cached_engines"] == 5

    def test_load_nats_config_clamps_low(self, tmp_path):
        """max_cached_engines clamped to 1 when set lower."""
        # Arrange
        from voicecli.config import load_nats_config

        cfg = tmp_path / "voicecli.toml"
        cfg.write_text("[nats]\nmax_cached_engines = 0\n")

        # Act
        result = load_nats_config(cfg)

        # Assert
        assert result["max_cached_engines"] == 1

    def test_load_tts_config_defaults(self, tmp_path, monkeypatch):
        """load_tts_config returns None for default_engine when no config."""
        # Arrange
        from voicecli.config import load_tts_config

        monkeypatch.setattr("voicecli.config._find_config", lambda: None)

        # Act
        result = load_tts_config()

        # Assert
        assert result["default_engine"] is None


class TestModelRegistryCore:
    """Tests for core ModelRegistry functionality."""

    def test_get_returns_cached_on_second_call(self):
        """Second get() for same engine returns cached instance (no reload)."""
        # Arrange
        from voicecli.model_registry import ModelRegistry

        registry = ModelRegistry()
        mock_engine = MagicMock()

        with patch("voicecli.engine._get_registry") as mock_reg:
            mock_reg.return_value = {"mock": lambda: mock_engine}
            # Act
            first = registry.get("mock")
            second = registry.get("mock")

        # Assert
        assert first is second
        mock_reg.assert_called_once()

    def test_get_raises_for_unknown_engine(self):
        """get() for unknown engine raises ValueError with list."""
        # Arrange
        from voicecli.model_registry import ModelRegistry

        registry = ModelRegistry()

        # Act + Assert
        with pytest.raises(ValueError, match="Unknown engine"):
            registry.get("nonexistent")

    def test_loaded_engines_returns_cached_names(self):
        """loaded_engines() returns list of cached engine names."""
        # Arrange
        from voicecli.model_registry import ModelRegistry

        registry = ModelRegistry()
        mock_engine = MagicMock()

        with patch("voicecli.engine._get_registry") as mock_reg:
            mock_reg.return_value = {"mock": lambda: mock_engine}
            # Act
            registry.get("mock")

        # Assert
        assert registry.loaded_engines() == ["mock"]
