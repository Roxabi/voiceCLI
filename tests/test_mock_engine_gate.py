"""RED-phase tests for MockEngine env-var gate (#45).

These tests verify the expected behaviour once VOICECLI_ENABLE_MOCK_ENGINE
is implemented as a runtime gate in _get_registry().  They are intentionally
written to FAIL against the current unmodified codebase (RED phase).
"""

from __future__ import annotations

import pytest

from voicecli.engine import available_engines, get_engine


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
    """With the gate off, passing model='mock' to transcribe() must raise."""
    monkeypatch.delenv("VOICECLI_ENABLE_MOCK_ENGINE", raising=False)

    audio_file = tmp_path / "silence.wav"
    audio_file.write_bytes(b"")

    import voicecli.transcribe

    with pytest.raises(Exception):
        voicecli.transcribe.transcribe(audio_file, model="mock")
