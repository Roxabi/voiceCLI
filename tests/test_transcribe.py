"""Tests for voicecli.transcribe — V2 VRAM lifecycle (issue #36).

RED phase: unload_model() and a threading lock on _load_model() do not exist
yet. These tests will fail with AttributeError/ImportError until the GREEN
phase lands those additions.

Strategy:
- Manipulate _model_cache and _model_lock directly on the module to avoid
  test pollution; reset in teardown.
- Mock faster_whisper.WhisperModel and torch.cuda — no GPU required.
- Use threading to validate that concurrent _load_model() calls hit the
  constructor exactly once.
"""

from __future__ import annotations

import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("torch", reason="torch is opt-in via [stt]/[tts]/[all] extras")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_transcribe_mod():
    """Return the real voicecli.transcribe module object.

    voicecli/__init__.py re-exports a `transcribe` *function* from voicecli.api
    which shadows the submodule attribute on the voicecli package.  We must
    reach the module through sys.modules to bypass that shadowing.
    """
    import voicecli.transcribe  # ensure the module is registered  # noqa: F401

    return sys.modules["voicecli.transcribe"]


def _reset_transcribe_state():
    """Clear _model_cache and reset _model_lock on the transcribe module."""
    t = _get_transcribe_mod()
    t._model_cache.clear()
    # _model_lock is a V2 addition; guard against AttributeError in RED phase
    if hasattr(t, "_model_lock"):
        # Replace with a fresh lock so tests start in unlocked state
        t._model_lock = threading.Lock()


# ---------------------------------------------------------------------------
# T06-1 — unload_model clears the cache and calls empty_cache
# ---------------------------------------------------------------------------


class TestUnloadModel:
    def setup_method(self):
        _reset_transcribe_state()

    def teardown_method(self):
        _reset_transcribe_state()

    def test_unload_model_clears_cache(self):
        """unload_model() empties _model_cache and calls torch.cuda.empty_cache."""
        # Arrange
        transcribe_mod = _get_transcribe_mod()

        mock_model = MagicMock()
        transcribe_mod._model_cache["large-v3-turbo"] = mock_model

        mock_cuda = MagicMock()
        mock_cuda.is_available.return_value = True

        with patch("torch.cuda", mock_cuda):
            # Act
            transcribe_mod.unload_model()

        # Assert
        assert transcribe_mod._model_cache == {}, (
            "Expected _model_cache to be empty after unload_model()"
        )
        mock_cuda.empty_cache.assert_called_once()

    def test_unload_model_noop_when_empty(self):
        """unload_model() returns early and does NOT call empty_cache when cache is empty."""
        # Arrange
        transcribe_mod = _get_transcribe_mod()

        assert transcribe_mod._model_cache == {}

        mock_cuda = MagicMock()
        mock_cuda.is_available.return_value = True

        with patch("torch.cuda", mock_cuda):
            # Act — should not raise
            transcribe_mod.unload_model()

        # Assert — no-op: empty_cache must NOT be called
        mock_cuda.empty_cache.assert_not_called()


# ---------------------------------------------------------------------------
# T06-2 — _load_model lock prevents double construction under concurrency
# ---------------------------------------------------------------------------


class TestLoadModelLock:
    def setup_method(self):
        _reset_transcribe_state()

    def teardown_method(self):
        _reset_transcribe_state()

    def test_load_model_lock_prevents_double_load(self):
        """Concurrent calls to _load_model() with the same model name construct
        WhisperModel exactly once — the lock ensures single-winner loading.

        RED: _load_model currently has no lock, so a race condition can cause
        the constructor to be called twice. This test will pass once V2 adds
        _model_lock around the cache-miss branch.
        """
        # Arrange
        transcribe_mod = _get_transcribe_mod()

        constructor_call_count = 0
        constructor_started = threading.Event()
        constructor_proceed = threading.Event()

        def slow_whisper_constructor(model_name, device, compute_type):
            nonlocal constructor_call_count
            constructor_call_count += 1
            constructor_started.set()
            # Hold the constructor until the second thread has had a chance to
            # also check the cache (simulating a race).
            constructor_proceed.wait(timeout=2.0)
            mock = MagicMock()
            mock.model_name = model_name
            return mock

        mock_whisper_cls = MagicMock(side_effect=slow_whisper_constructor)

        results: list = []
        errors: list = []

        def load_in_thread():
            try:
                result = transcribe_mod._load_model("large-v3-turbo")
                results.append(result)
            except Exception as exc:
                errors.append(exc)

        with patch("faster_whisper.WhisperModel", mock_whisper_cls):
            # Act — launch two threads simultaneously
            t1 = threading.Thread(target=load_in_thread)
            t2 = threading.Thread(target=load_in_thread)

            t1.start()
            # Wait until the first thread is inside the constructor before
            # starting the second, ensuring both compete for the cache slot.
            constructor_started.wait(timeout=2.0)
            t2.start()

            # Give t2 a moment to reach the lock contention point, then release
            # the constructor so both threads can complete.
            time.sleep(0.05)
            constructor_proceed.set()

            t1.join(timeout=3.0)
            t2.join(timeout=3.0)

        # Assert
        assert not t1.is_alive(), "Thread 1 did not finish within timeout"
        assert not t2.is_alive(), "Thread 2 did not finish within timeout"
        assert not errors, f"Thread(s) raised: {errors}"
        assert len(results) == 2, "Both threads should have received a model"

        # The constructor must have been called exactly once
        assert constructor_call_count == 1, (
            f"WhisperModel constructor called {constructor_call_count} times — "
            "expected 1. The lock is missing or ineffective."
        )

        # Both threads must have received the same model instance
        assert results[0] is results[1], (
            "Threads received different model instances — lock not preventing double load."
        )
