"""Unit tests for LocalSynthesisAdapter."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    engine = MagicMock()
    engine.generate.return_value = Path("/tmp/out.wav")
    engine.clone.return_value = Path("/tmp/out.wav")
    registry.get.return_value = engine
    return registry, engine


class TestLocalSynthesisAdapterIsAvailable:
    def test_always_true(self):
        from voicecli.adapters.synthesis import LocalSynthesisAdapter

        registry = MagicMock()
        adapter = LocalSynthesisAdapter(registry)
        assert adapter.is_available() is True


class TestLocalSynthesisAdapterGenerate:
    def test_delegates_to_registry_engine(self, mock_registry):
        from voicecli.adapters.synthesis import LocalSynthesisAdapter

        registry, engine = mock_registry
        adapter = LocalSynthesisAdapter(registry)
        out = Path("/tmp/out.wav")

        result = adapter.generate("chatterbox", "hello", "Ryan", out)

        registry.get.assert_called_once_with("chatterbox")
        engine.generate.assert_called_once()
        assert result == out

    def test_fast_sets_small_for_qwen(self, mock_registry):
        from voicecli.adapters.synthesis import LocalSynthesisAdapter

        registry, engine = mock_registry
        adapter = LocalSynthesisAdapter(registry)
        out = Path("/tmp/out.wav")

        with patch("voicecli.engine.QWEN_ENGINES", {"qwen"}):
            adapter.generate("qwen", "hello", None, out, fast=True)

        engine.set_small_mode.assert_called_once()

    def test_fast_not_forwarded_to_engine(self, mock_registry):
        from voicecli.adapters.synthesis import LocalSynthesisAdapter

        registry, engine = mock_registry
        adapter = LocalSynthesisAdapter(registry)
        out = Path("/tmp/out.wav")

        with patch("voicecli.engine.QWEN_ENGINES", {"qwen"}):
            adapter.generate("qwen", "hello", None, out, fast=True)

        call_kwargs = engine.generate.call_args[1]
        assert "fast" not in call_kwargs

    def test_fast_ignored_for_non_qwen(self, mock_registry):
        from voicecli.adapters.synthesis import LocalSynthesisAdapter

        registry, engine = mock_registry
        adapter = LocalSynthesisAdapter(registry)
        out = Path("/tmp/out.wav")

        with patch("voicecli.engine.QWEN_ENGINES", {"qwen"}):
            adapter.generate("chatterbox", "hello", None, out, fast=True)

        assert not hasattr(engine, "_small") or engine._small is not True


class TestLocalSynthesisAdapterClone:
    def test_delegates_to_registry_engine(self, mock_registry):
        from voicecli.adapters.synthesis import LocalSynthesisAdapter

        registry, engine = mock_registry
        adapter = LocalSynthesisAdapter(registry)
        ref = Path("/tmp/ref.wav")
        out = Path("/tmp/out.wav")

        result = adapter.clone("chatterbox", "hello", ref, out)

        registry.get.assert_called_once_with("chatterbox")
        engine.clone.assert_called_once()
        assert result == out

    def test_fast_sets_small_for_qwen(self, mock_registry):
        from voicecli.adapters.synthesis import LocalSynthesisAdapter

        registry, engine = mock_registry
        adapter = LocalSynthesisAdapter(registry)
        ref = Path("/tmp/ref.wav")
        out = Path("/tmp/out.wav")

        with patch("voicecli.engine.QWEN_ENGINES", {"qwen"}):
            adapter.clone("qwen", "hello", ref, out, fast=True)

        engine.set_small_mode.assert_called_once()

    def test_fast_not_forwarded_to_engine(self, mock_registry):
        from voicecli.adapters.synthesis import LocalSynthesisAdapter

        registry, engine = mock_registry
        adapter = LocalSynthesisAdapter(registry)
        ref = Path("/tmp/ref.wav")
        out = Path("/tmp/out.wav")

        with patch("voicecli.engine.QWEN_ENGINES", {"qwen"}):
            adapter.clone("qwen", "hello", ref, out, fast=True)

        call_kwargs = engine.clone.call_args[1]
        assert "fast" not in call_kwargs
