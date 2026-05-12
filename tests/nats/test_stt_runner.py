"""Unit tests for voicecli.nats._stt_runner (issue #147).

Lifecycle invariant (pinned by spec): the adapter creates and cleans up the
on-disk audio file. The runner writes the decoded bytes via scoped_path but
does NOT call cleanup() — the adapter wraps run_transcription in try/finally.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from _fakes import SyncExecutor

from voicecli.nats._stt_runner import (
    SttRunnerState,
    run_transcription,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeApi:
    """Capture api.transcribe + api.warmup_model calls."""

    _transcribe_behavior: Callable[..., Any] | None
    _warmup_behavior: Callable[..., None] | None

    def __init__(self) -> None:
        self.transcribe_calls: list[dict] = []
        self.warmup_calls: list[str] = []
        self._transcribe_behavior = None
        self._warmup_behavior = None

    def transcribe(self, out_path: Path, *, model: str, _skip_daemon: bool = True, **overrides):
        self.transcribe_calls.append({"out_path": out_path, "model": model, **overrides})
        if self._transcribe_behavior is not None:
            return self._transcribe_behavior(out_path, model=model, **overrides)
        # Default: minimal TranscriptionResult-shaped object
        from voicecli.transcribe import TranscriptionResult

        return TranscriptionResult(text="hello", language="en", segments=[{"end": 1.5}])

    def warmup_model(self, model: str) -> None:
        self.warmup_calls.append(model)
        if self._warmup_behavior is not None:
            self._warmup_behavior(model)


def _make_state(
    *,
    model_warm: bool = False,
    set_model_warm: Callable[[bool], None] | None = None,
    set_model_loaded: Callable[[str | None], None] | None = None,
) -> "SttRunnerState":
    """Build an SttRunnerState with recorded callbacks."""
    warm_calls: list[bool] = []
    loaded_calls: list[str | None] = []

    cb_warm = set_model_warm or (lambda v: warm_calls.append(v))
    cb_loaded = set_model_loaded or (lambda m: loaded_calls.append(m))

    state = SttRunnerState(  # type: ignore[call-arg]
        executor=SyncExecutor(),  # type: ignore[arg-type]
        set_model_warm=cb_warm,
        set_model_loaded=cb_loaded,
        model_warm=model_warm,
    )
    # Attach the recorded lists for easy inspection in tests
    state._warm_calls = warm_calls  # type: ignore[attr-defined]
    state._loaded_calls = loaded_calls  # type: ignore[attr-defined]
    return state


def _valid_audio_b64() -> str:
    """Return valid base64 of a small WAV-ish byte blob."""
    return base64.b64encode(b"\x00" * 32).decode()


def _run(coro):
    """Run a coroutine in a fresh event loop and return the result."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_api(monkeypatch):
    fake = _FakeApi()
    monkeypatch.setattr("voicecli.api.transcribe", fake.transcribe)
    monkeypatch.setattr("voicecli.api.warmup_model", fake.warmup_model)
    return fake


@pytest.fixture
def patch_scoped_path(tmp_path, monkeypatch):
    """Redirect scoped_path so the runner writes into tmp_path."""

    def _impl(request_id: str, ext: str) -> Path:
        return tmp_path / f"{request_id}.{ext}"

    monkeypatch.setattr("voicecli.nats.tempdir.scoped_path", _impl)
    return _impl


# ===========================================================================
# TestRunTranscriptionHappyPath
# ===========================================================================


class TestRunTranscriptionHappyPath:
    def test_happy_path_returns_true_with_fields(
        self, fake_api: _FakeApi, patch_scoped_path
    ) -> None:
        # Arrange
        state = _make_state()
        audio_b64 = _valid_audio_b64()

        # Act
        ok, result = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                1024,
                {"mime_type": "audio/wav"},
                "req-001",
                audio_b64,
                {},
                trace_id="t1",
            )
        )

        # Assert
        assert ok is True
        assert isinstance(result, dict)
        assert result["text"] == "hello"
        assert result["language"] == "en"
        assert "duration_seconds" in result
        assert isinstance(result["duration_seconds"], float)

    def test_happy_path_calls_set_model_warm_and_set_model_loaded(
        self, fake_api: _FakeApi, patch_scoped_path
    ) -> None:
        # Arrange
        warm_calls: list[bool] = []
        loaded_calls: list[str | None] = []

        state = _make_state(
            set_model_warm=lambda v: warm_calls.append(v),
            set_model_loaded=lambda m: loaded_calls.append(m),
        )

        # Act
        ok, result = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                1024,
                {"mime_type": "audio/wav"},
                "req-002",
                _valid_audio_b64(),
                {},
                trace_id="t2",
            )
        )

        # Assert
        assert ok is True
        assert warm_calls == [True], f"expected set_model_warm(True) once, got {warm_calls}"
        assert loaded_calls == ["large-v3-turbo"], (
            f"expected set_model_loaded('large-v3-turbo') once, got {loaded_calls}"
        )

    def test_warm_model_skips_warmup_on_second_call(
        self, fake_api: _FakeApi, patch_scoped_path
    ) -> None:
        # Arrange — model already warm; warmup_model must NOT be called
        state = _make_state(model_warm=True)
        audio_b64 = _valid_audio_b64()

        # Act
        ok, result = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                1024,
                {},
                "req-003",
                audio_b64,
                {},
                trace_id="t3",
            )
        )

        # Assert
        assert ok is True
        assert fake_api.warmup_calls == [], (
            "warmup_model must not be called when state.model_warm is True"
        )

    @pytest.mark.parametrize(
        "mime_type,expected_ext",
        [
            ("audio/wav", "wav"),
            ("audio/mp3", "mp3"),
        ],
        ids=["wav", "mp3"],
    )
    def test_mime_type_determines_file_extension(
        self,
        tmp_path: Path,
        monkeypatch,
        fake_api: _FakeApi,
        mime_type: str,
        expected_ext: str,
    ) -> None:
        # Arrange — capture the path that scoped_path produces
        observed_ext: list[str] = []

        def _capturing_scoped_path(request_id: str, ext: str) -> Path:
            observed_ext.append(ext)
            return tmp_path / f"{request_id}.{ext}"

        monkeypatch.setattr("voicecli.nats.tempdir.scoped_path", _capturing_scoped_path)

        state = _make_state()

        # Act
        ok, _result = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                1024,
                {"mime_type": mime_type},
                "req-mime",
                _valid_audio_b64(),
                {},
                trace_id="t-mime",
            )
        )

        # Assert
        assert ok is True
        assert observed_ext == [expected_ext], (
            f"expected ext={expected_ext!r} for mime_type={mime_type!r}, got {observed_ext}"
        )


# ===========================================================================
# TestRunTranscriptionPayloadTooLarge
# ===========================================================================


class TestRunTranscriptionPayloadTooLarge:
    def test_audio_b64_exceeds_cap_returns_payload_too_large(
        self, fake_api: _FakeApi, patch_scoped_path
    ) -> None:
        # Arrange — cap is 100 bytes, audio_b64 is 200 chars
        cap = 100
        audio_b64 = "A" * 200
        state = _make_state()

        # Act
        ok, error_code = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                cap,
                {},
                "req-toobig",
                audio_b64,
                {},
                trace_id="t-big",
            )
        )

        # Assert
        assert ok is False
        assert error_code == "payload_too_large"

    def test_payload_too_large_does_not_call_transcribe_or_warmup(
        self, fake_api: _FakeApi, patch_scoped_path
    ) -> None:
        # Arrange
        cap = 10
        audio_b64 = "B" * 200
        state = _make_state()

        # Act
        ok, error_code = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                cap,
                {},
                "req-toobig2",
                audio_b64,
                {},
                trace_id="t-big2",
            )
        )

        # Assert
        assert ok is False
        assert error_code == "payload_too_large"
        assert fake_api.transcribe_calls == [], (
            "api.transcribe must not be called for oversized payload"
        )
        assert fake_api.warmup_calls == [], "warmup_model must not be called for oversized payload"


# ===========================================================================
# TestRunTranscriptionAudioDecode
# ===========================================================================


class TestRunTranscriptionAudioDecode:
    def test_invalid_base64_returns_audio_decode_failed(
        self, fake_api: _FakeApi, patch_scoped_path
    ) -> None:
        # Arrange
        state = _make_state()
        bad_b64 = "not===base64@@@"

        # Act
        ok, error_code = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                1024,
                {},
                "req-decode",
                bad_b64,
                {},
                trace_id="t-dec",
            )
        )

        # Assert
        assert ok is False
        assert error_code == "audio_decode_failed"
        assert fake_api.transcribe_calls == [], (
            "api.transcribe must not be called on decode failure"
        )


# ===========================================================================
# TestRunTranscriptionModelLoad
# ===========================================================================


class TestRunTranscriptionModelLoad:
    def test_warmup_raises_returns_model_load_failed(self, patch_scoped_path, monkeypatch) -> None:
        # Arrange
        warm_calls: list[bool] = []
        loaded_calls: list[str | None] = []
        state = _make_state(
            set_model_warm=lambda v: warm_calls.append(v),
            set_model_loaded=lambda m: loaded_calls.append(m),
        )

        transcribe_calls: list = []

        def _fail_warmup(model: str) -> None:
            raise RuntimeError("GPU OOM during warmup")

        def _fake_transcribe(out_path, *, model, _skip_daemon=True, **kw):
            transcribe_calls.append(model)
            from voicecli.transcribe import TranscriptionResult

            return TranscriptionResult(text="x", language="en", segments=[])

        monkeypatch.setattr("voicecli.api.warmup_model", _fail_warmup)
        monkeypatch.setattr("voicecli.api.transcribe", _fake_transcribe)

        # Act
        ok, error_code = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                1024,
                {},
                "req-warmfail",
                _valid_audio_b64(),
                {},
                trace_id="t-wf",
            )
        )

        # Assert
        assert ok is False
        assert error_code == "model_load_failed"
        assert True not in warm_calls, "set_model_warm(True) must not be called when warmup fails"
        assert not loaded_calls, "set_model_loaded must not be called when warmup fails"
        assert transcribe_calls == [], "api.transcribe must not be called when model load fails"

    def test_two_consecutive_failing_warmups_attempt_warmup_twice(
        self, patch_scoped_path, monkeypatch
    ) -> None:
        # Arrange — each call should attempt warmup; no backoff or caching of failure
        warmup_attempt_count = 0

        def _fail_warmup(model: str) -> None:
            nonlocal warmup_attempt_count
            warmup_attempt_count += 1
            raise RuntimeError("still failing")

        monkeypatch.setattr("voicecli.api.warmup_model", _fail_warmup)

        state = _make_state()
        audio_b64 = _valid_audio_b64()

        # Act — two separate calls; model_warm stays False after each failure
        _run(
            run_transcription(
                state, "large-v3-turbo", 1024, {}, "req-wf-retry1", audio_b64, {}, trace_id="t1"
            )
        )
        _run(
            run_transcription(
                state, "large-v3-turbo", 1024, {}, "req-wf-retry2", audio_b64, {}, trace_id="t2"
            )
        )

        # Assert — each call re-attempted warmup (2 total)
        assert warmup_attempt_count == 2, (
            f"expected 2 warmup attempts across 2 failing calls, got {warmup_attempt_count}"
        )


# ===========================================================================
# TestRunTranscriptionParamValidation
# ===========================================================================


class TestRunTranscriptionParamValidation:
    def test_param_validation_error_returns_param_validation_failed(
        self, patch_scoped_path, monkeypatch
    ) -> None:
        # Arrange
        from voicecli.api import ParamValidationError

        def _bad_transcribe(out_path, *, model, _skip_daemon=True, **kw):
            raise ParamValidationError("bad lang")

        monkeypatch.setattr("voicecli.api.warmup_model", lambda m: None)
        monkeypatch.setattr("voicecli.api.transcribe", _bad_transcribe)

        state = _make_state()

        # Act
        ok, error_code = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                1024,
                {},
                "req-val",
                _valid_audio_b64(),
                {},
                trace_id="t-val",
            )
        )

        # Assert
        assert ok is False
        assert error_code == "param_validation_failed"


# ===========================================================================
# TestRunTranscriptionGenericError
# ===========================================================================


class TestRunTranscriptionGenericError:
    def test_runtime_error_returns_transcription_failed(
        self, patch_scoped_path, monkeypatch
    ) -> None:
        # Arrange
        def _crash_transcribe(out_path, *, model, _skip_daemon=True, **kw):
            raise RuntimeError("model OOM during inference")

        monkeypatch.setattr("voicecli.api.warmup_model", lambda m: None)
        monkeypatch.setattr("voicecli.api.transcribe", _crash_transcribe)

        state = _make_state()

        # Act
        ok, error_code = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                1024,
                {},
                "req-crash",
                _valid_audio_b64(),
                {},
                trace_id="t-crash",
            )
        )

        # Assert
        assert ok is False
        assert error_code == "transcription_failed"


# ===========================================================================
# TestRunTranscriptionOverridesForwarding
# ===========================================================================


class TestRunTranscriptionOverridesForwarding:
    def test_language_override_forwarded_to_transcribe(
        self, patch_scoped_path, monkeypatch
    ) -> None:
        # Arrange
        captured: dict = {}

        def _capturing_transcribe(out_path, *, model, _skip_daemon=True, **kw):
            captured.update(kw)
            from voicecli.transcribe import TranscriptionResult

            return TranscriptionResult(text="bonjour", language="fr", segments=[{"end": 1.0}])

        monkeypatch.setattr("voicecli.api.warmup_model", lambda m: None)
        monkeypatch.setattr("voicecli.api.transcribe", _capturing_transcribe)

        state = _make_state()
        overrides = {"language": "fr"}

        # Act
        ok, result = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                1024,
                {},
                "req-fr",
                _valid_audio_b64(),
                overrides,
                trace_id="t-fr",
            )
        )

        # Assert
        assert ok is True
        assert captured.get("language") == "fr", (
            f"expected language='fr' forwarded to api.transcribe, got {captured}"
        )

    def test_empty_overrides_no_extra_kwargs(self, patch_scoped_path, monkeypatch) -> None:
        # Arrange
        captured: dict = {}

        def _capturing_transcribe(out_path, *, model, _skip_daemon=True, **kw):
            captured.update(kw)
            from voicecli.transcribe import TranscriptionResult

            return TranscriptionResult(text="hi", language="en", segments=[{"end": 0.5}])

        monkeypatch.setattr("voicecli.api.warmup_model", lambda m: None)
        monkeypatch.setattr("voicecli.api.transcribe", _capturing_transcribe)

        state = _make_state()

        # Act
        ok, _result = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                1024,
                {},
                "req-empty-ov",
                _valid_audio_b64(),
                {},
                trace_id="t-empty",
            )
        )

        # Assert
        assert ok is True
        # No unexpected override keys leaked in
        for override_key in ("language", "task", "initial_prompt", "language_fallback"):
            assert override_key not in captured, (
                f"'{override_key}' must not appear in api.transcribe kwargs for empty overrides"
            )

    def test_multiple_overrides_forwarded_together(self, patch_scoped_path, monkeypatch) -> None:
        # Arrange
        captured: dict = {}

        def _capturing_transcribe(out_path, *, model, _skip_daemon=True, **kw):
            captured.update(kw)
            from voicecli.transcribe import TranscriptionResult

            return TranscriptionResult(text="hallo", language="de", segments=[{"end": 2.0}])

        monkeypatch.setattr("voicecli.api.warmup_model", lambda m: None)
        monkeypatch.setattr("voicecli.api.transcribe", _capturing_transcribe)

        state = _make_state()
        overrides = {
            "language": "de",
            "task": "transcribe",
            "initial_prompt": "Guten Tag.",
            "language_detection_threshold": 0.7,
        }

        # Act
        ok, result = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                1024,
                {},
                "req-multi-ov",
                _valid_audio_b64(),
                overrides,
                trace_id="t-multi",
            )
        )

        # Assert
        assert ok is True
        assert captured.get("language") == "de"
        assert captured.get("task") == "transcribe"
        assert captured.get("initial_prompt") == "Guten Tag."
        assert captured.get("language_detection_threshold") == pytest.approx(0.7)
