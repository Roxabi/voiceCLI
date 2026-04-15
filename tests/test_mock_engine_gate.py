"""Tests for MockEngine isolation and fixture activation (#56).

These tests verify:
1. MockEngine is not available in production code (no env var gate)
2. MockEngine can be activated via pytest fixture
3. The WAV header contract for silent WAV generation
"""

from __future__ import annotations

import sys

import pytest

import voicecli.transcribe  # noqa: F401 — registers submodule in sys.modules
from voicecli.engine import available_engines, get_engine

# voicecli/__init__.py overwrites the `voicecli.transcribe` attribute with the
# `transcribe` function, so resolve the actual module via sys.modules.
_transcribe_mod = sys.modules["voicecli.transcribe"]


def test_prod_registry_excludes_mock():
    """Production code never includes 'mock' in the engine registry."""
    assert "mock" not in available_engines()

    with pytest.raises(ValueError, match="Unknown engine 'mock'"):
        get_engine("mock")


def test_stt_fallthrough_without_fixture(monkeypatch, tmp_path):
    """Without fixture, passing model='mock' to transcribe() must raise a
    real error — proving the mock branch is not in production code.
    Stub _try_daemon to None so the path is forced through _load_model.
    """
    monkeypatch.setattr(_transcribe_mod, "_try_daemon", lambda *a, **kw: None)

    audio_file = tmp_path / "silence.wav"
    audio_file.write_bytes(b"")

    with pytest.raises((ValueError, RuntimeError, OSError)):
        _transcribe_mod.transcribe(audio_file, model="mock")


def test_mock_engine_fixture_activates_mock(mock_engine):
    """When mock_engine fixture is active, 'mock' appears in registry."""
    assert "mock" in available_engines()
    engine = get_engine("mock")
    assert engine.name == "mock"


def test_silent_wav_bytes_header():
    """Lock the WAV header contract — any struct.pack drift breaks this."""
    from tests.engines.mock import _SILENT_WAV, _silent_wav_bytes

    data = _silent_wav_bytes()

    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"
    assert data[12:16] == b"fmt "
    assert data[36:40] == b"data"
    assert len(data) == 44 + 22050 * 2  # 44-byte header + 1s 16-bit mono @ 22.05 kHz
    assert _SILENT_WAV == data
