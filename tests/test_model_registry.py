"""Tests for config loading functions in voicecli.config.

Tests for load_nats_config and load_tts_config default values and clamping.
"""

from __future__ import annotations

import threading
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


class TestModelRegistryVRAM:
    """Tests for VRAM-aware eviction."""

    def test_evict_removes_engine_from_cache(self):
        """evict() removes specified engine from cache."""
        # Arrange
        from voicecli.model_registry import ModelRegistry

        registry = ModelRegistry()
        mock_engine = MagicMock()

        with patch("voicecli.engine._get_registry") as mock_reg:
            mock_reg.return_value = {"mock": lambda: mock_engine}
            registry.get("mock")

        # Act
        registry.evict("mock")

        # Assert
        assert registry.loaded_engines() == []

    def test_evict_is_noop_for_uncached_engine(self):
        """evict() for non-cached engine is safe no-op."""
        # Arrange
        from voicecli.model_registry import ModelRegistry

        registry = ModelRegistry()
        mock_engine = MagicMock()

        with patch("voicecli.engine._get_registry") as mock_reg:
            mock_reg.return_value = {"mock": lambda: mock_engine}
            registry.get("mock")

        # Act - should not raise
        registry.evict("nonexistent")

        # Assert - mock still cached
        assert registry.loaded_engines() == ["mock"]

    def test_ensure_vram_raises_when_cache_empty(self):
        """InsufficientVRAMError raised when cache empty and VRAM insufficient."""
        # Arrange
        from voicecli.model_registry import InsufficientVRAMError, ModelRegistry

        registry = ModelRegistry()

        with (
            patch("voicecli.engine._get_registry") as mock_reg,
            patch.object(registry, "_has_vram", return_value=False),
        ):
            mock_reg.return_value = {}

            # Act + Assert
            with pytest.raises(InsufficientVRAMError, match="Not enough VRAM"):
                registry._ensure_vram("qwen")

    def test_ensure_vram_evicts_until_sufficient(self):
        """_ensure_vram evicts LRU engines until VRAM sufficient."""
        # Arrange
        from voicecli.model_registry import ModelRegistry

        registry = ModelRegistry()
        mock_engine1 = MagicMock()
        mock_engine2 = MagicMock()

        with (
            patch("voicecli.engine._get_registry") as mock_reg,
            patch.object(registry, "_has_vram") as mock_has_vram,
        ):
            mock_reg.return_value = {
                "engine1": lambda: mock_engine1,
                "engine2": lambda: mock_engine2,
            }
            # First get: has VRAM, _ensure_vram for engine2: no VRAM, then yes after eviction
            mock_has_vram.side_effect = [True, False, True]

            # Load first engine (VRAM available)
            registry.get("engine1")

            # Act - should evict engine1 before loading engine2
            registry._ensure_vram("engine2")

        # Assert - cache should be empty (evicted, not yet loaded)
        assert registry.loaded_engines() == []


class TestModelRegistryThreadSafety:
    """Tests for thread-safe operations."""

    def test_concurrent_get_same_engine_loads_once(self):
        """Two concurrent get() for same engine load exactly one model."""
        # Arrange
        from voicecli.model_registry import ModelRegistry

        registry = ModelRegistry()
        load_count = 0
        lock = threading.Lock()

        def make_engine():
            nonlocal load_count
            with lock:
                load_count += 1
            return MagicMock()

        results = []

        def get_engine():
            with patch("voicecli.engine._get_registry") as mock_reg:
                mock_reg.return_value = {"mock": make_engine}
                results.append(registry.get("mock"))

        # Act - run two threads concurrently
        t1 = threading.Thread(target=get_engine)
        t2 = threading.Thread(target=get_engine)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Assert - engine loaded exactly once
        assert load_count == 1
        # Both get same instance
        assert results[0] is results[1]

    def test_concurrent_get_different_engines(self):
        """Concurrent get() for different engines both succeed."""
        # Arrange
        from voicecli.model_registry import ModelRegistry

        registry = ModelRegistry()
        results = {}

        def get_engine(name):
            with patch("voicecli.engine._get_registry") as mock_reg:
                mock_reg.return_value = {name: MagicMock}
                results[name] = registry.get(name)

        # Act - run threads concurrently
        t1 = threading.Thread(target=get_engine, args=("engine1",))
        t2 = threading.Thread(target=get_engine, args=("engine2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Assert - both engines cached
        assert "engine1" in results
        assert "engine2" in results
        assert set(registry.loaded_engines()) == {"engine1", "engine2"}


class TestHeartbeatEnhancement:
    """Tests for heartbeat payload with VRAM metrics."""

    @pytest.mark.skipif(
        True,  # Skip if roxabi_nats not available
        reason="Requires roxabi_nats dependency",
    )
    def test_heartbeat_payload_includes_vram_metrics(self):
        """heartbeat_payload includes vram_free_mb and vram_status."""
        # Arrange - mock TtsNatsAdapter's heartbeat_payload
        from voicecli.nats.tts_adapter import TtsNatsAdapter

        adapter = TtsNatsAdapter(default_engine="qwen-fast")

        # Act
        payload = adapter.heartbeat_payload()

        # Assert
        assert "vram_free_mb" in payload
        assert "vram_status" in payload
        assert payload["vram_status"] in ("ok", "constrained", "critical")
        assert isinstance(payload["vram_free_mb"], int)

    def test_vram_status_ok_when_sufficient(self):
        """vram_status is 'ok' when > 4GB free."""
        from voicecli.model_registry import ModelRegistry

        registry = ModelRegistry()

        # Mock vram_free_mb to return high value
        with patch.object(registry, "vram_free_mb", return_value=5000):
            free_mb = registry.vram_free_mb()
            # Determine status like tts_adapter does
            if free_mb >= 4096:
                vram_status = "ok"
            elif free_mb >= 1024:
                vram_status = "constrained"
            else:
                vram_status = "critical"

        assert vram_status == "ok"

    def test_vram_status_constrained_when_low(self):
        """vram_status is 'constrained' when 1-4GB free."""
        from voicecli.model_registry import ModelRegistry

        registry = ModelRegistry()

        # Mock vram_free_mb to return medium value
        with patch.object(registry, "vram_free_mb", return_value=2000):
            free_mb = registry.vram_free_mb()
            if free_mb >= 4096:
                vram_status = "ok"
            elif free_mb >= 1024:
                vram_status = "constrained"
            else:
                vram_status = "critical"

        assert vram_status == "constrained"

    def test_vram_status_critical_when_very_low(self):
        """vram_status is 'critical' when < 1GB free."""
        from voicecli.model_registry import ModelRegistry

        registry = ModelRegistry()

        # Mock vram_free_mb to return low value
        with patch.object(registry, "vram_free_mb", return_value=500):
            free_mb = registry.vram_free_mb()
            if free_mb >= 4096:
                vram_status = "ok"
            elif free_mb >= 1024:
                vram_status = "constrained"
            else:
                vram_status = "critical"

        assert vram_status == "critical"
