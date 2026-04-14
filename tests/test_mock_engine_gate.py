"""RED-phase tests for MockEngine env-var gate (#45).

These tests verify the expected behaviour once VOICECLI_ENABLE_MOCK_ENGINE
is implemented as a runtime gate in _get_registry().  They are intentionally
written to FAIL against the current unmodified codebase (RED phase).
"""

from __future__ import annotations

import sys

import pytest

import voicecli.transcribe  # noqa: F401 — registers submodule in sys.modules
from voicecli.engine import available_engines, get_engine

# voicecli/__init__.py overwrites the `voicecli.transcribe` attribute with the
# `transcribe` function, so resolve the actual module via sys.modules.
_transcribe_mod = sys.modules["voicecli.transcribe"]


def test_env_unset_from_start(monkeypatch):
    """When VOICECLI_ENABLE_MOCK_ENGINE is absent, 'mock' must not appear."""
    monkeypatch.delenv("VOICECLI_ENABLE_MOCK_ENGINE", raising=False)

    assert "mock" not in available_engines()

    with pytest.raises(ValueError, match="Unknown engine 'mock'"):
        get_engine("mock")


def test_env_set_then_unset(monkeypatch):
    """Setting the env adds 'mock'; unsetting it removes it (no caching)."""
    monkeypatch.setenv("VOICECLI_ENABLE_MOCK_ENGINE", "1")
    assert "mock" in available_engines()

    monkeypatch.delenv("VOICECLI_ENABLE_MOCK_ENGINE")
    assert "mock" not in available_engines()


def test_stt_fallthrough_when_unset(monkeypatch, tmp_path):
    """With the gate off, passing model='mock' to transcribe() must raise a
    real model-load / file error — proving the mock branch was NOT silently
    taken. Stub _try_daemon to None so the path is forced through _load_model.
    """
    monkeypatch.delenv("VOICECLI_ENABLE_MOCK_ENGINE", raising=False)
    monkeypatch.setattr(_transcribe_mod, "_try_daemon", lambda *a, **kw: None)

    audio_file = tmp_path / "silence.wav"
    audio_file.write_bytes(b"")

    with pytest.raises((ValueError, RuntimeError, OSError)):
        _transcribe_mod.transcribe(audio_file, model="mock")
