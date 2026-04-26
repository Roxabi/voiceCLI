"""Tests for the MockEngine env-var gate and fixture activation.

These tests verify:
1. MockEngine is not in the registry unless ``VOICECLI_ENABLE_MOCK_ENGINE=1``.
2. STT ``transcribe(model="mock")`` falls through to the real loader unless the
   gate is set, and short-circuits when the gate is set.
3. The ``mock_engine`` fixture activates the gate for test scope.
4. The silent-WAV header contract is stable.
"""

from __future__ import annotations

import sys

import pytest

import voicecli.transcribe  # noqa: F401 — registers submodule in sys.modules
from voicecli.engine import available_engines, get_engine

# voicecli/__init__.py overwrites the `voicecli.transcribe` attribute with the
# `transcribe` function, so resolve the actual module via sys.modules.
_transcribe_mod = sys.modules["voicecli.transcribe"]


def test_registry_excludes_mock_when_env_unset(monkeypatch):
    """Without the env gate, 'mock' is absent from the engine registry."""
    monkeypatch.delenv("VOICECLI_ENABLE_MOCK_ENGINE", raising=False)
    assert "mock" not in available_engines()

    # Without the gate, get_engine("mock") raises ValueError. Exact wording
    # depends on whether torch is installed: real engines registered →
    # "Unknown engine 'mock'"; torch absent (slim install) → "No engines
    # available". Both confirm the gate excludes mock.
    with pytest.raises(ValueError, match=r"(Unknown engine 'mock'|No engines available)"):
        get_engine("mock")


def test_registry_includes_mock_when_env_set(monkeypatch):
    """With the env gate, 'mock' is present and instantiable."""
    monkeypatch.setenv("VOICECLI_ENABLE_MOCK_ENGINE", "1")
    assert "mock" in available_engines()
    engine = get_engine("mock")
    assert engine.name == "mock"


def test_stt_fallthrough_when_env_unset(monkeypatch, tmp_path):
    """Without the env gate, transcribe(model='mock') reaches the real loader
    and raises — the mock short-circuit requires the env gate.

    Stub _try_daemon to None so the path is forced through _load_model.
    """
    monkeypatch.delenv("VOICECLI_ENABLE_MOCK_ENGINE", raising=False)
    monkeypatch.setattr(_transcribe_mod, "_try_daemon", lambda *a, **kw: None)

    audio_file = tmp_path / "silence.wav"
    audio_file.write_bytes(b"")

    with pytest.raises((ValueError, RuntimeError, OSError)):
        _transcribe_mod.transcribe(audio_file, model="mock")


def test_stt_short_circuits_when_env_set(monkeypatch, tmp_path):
    """With the env gate, transcribe(model='mock') returns an empty result
    without touching the loader or daemon — no real audio needed.
    """
    monkeypatch.setenv("VOICECLI_ENABLE_MOCK_ENGINE", "1")
    audio_file = tmp_path / "silence.wav"
    audio_file.write_bytes(b"")

    result = _transcribe_mod.transcribe(audio_file, model="mock")
    assert result.text == ""
    assert result.language == "en"
    assert result.segments == []


def test_mock_engine_fixture_activates_mock(mock_engine):
    """The shared ``mock_engine`` fixture exposes 'mock' in the registry."""
    assert "mock" in available_engines()
    engine = get_engine("mock")
    assert engine.name == "mock"


def test_silent_wav_bytes_header():
    """Lock the WAV header contract — any struct.pack drift breaks this."""
    from voicecli.engines.mock import _SILENT_WAV, _silent_wav_bytes

    data = _silent_wav_bytes()

    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"
    assert data[12:16] == b"fmt "
    assert data[36:40] == b"data"
    assert len(data) == 44 + 22050 * 2  # 44-byte header + 1s 16-bit mono @ 22.05 kHz
    assert _SILENT_WAV == data
