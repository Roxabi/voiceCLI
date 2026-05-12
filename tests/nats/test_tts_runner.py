"""Unit tests for voicecli.nats._tts_runner (issue #147)."""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import io
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from voicecli.nats._tts_runner import (
    NAMED_KWARGS,
    OPTIONAL_KWARGS,
    TtsRunnerState,
    run_synthesis,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _SyncExecutor:
    """Drop-in for ThreadPoolExecutor that runs submitted callables synchronously."""

    def submit(self, fn, *args, **kwargs):
        f: concurrent.futures.Future = concurrent.futures.Future()
        try:
            f.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001
            f.set_exception(exc)
        return f


class _FakeApi:
    """Captures api.generate call kwargs and delegates to a configurable behavior."""

    _behavior: Callable[..., None] | None

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._behavior = None  # set per-test

    def generate(self, text: str, *, engine: str, output: Path, **kw: Any) -> None:
        self.calls.append({"text": text, "engine": engine, "output": output, **kw})
        if self._behavior is not None:
            self._behavior(text, engine=engine, output=output, **kw)


def _make_state(*, set_model_loaded=None) -> "TtsRunnerState":
    recorded: list[str] = []
    cb = set_model_loaded or (lambda e: recorded.append(e))
    return TtsRunnerState(executor=_SyncExecutor(), set_model_loaded=cb)  # type: ignore[arg-type]


def _make_silent_wav_bytes(n_frames: int = 2205) -> bytes:
    """Build a minimal silent WAV (22050 Hz, 16-bit, mono)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(b"\x00\x00" * n_frames)
    return buf.getvalue()


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
    monkeypatch.setattr("voicecli.api.generate", fake.generate)
    return fake


# ===========================================================================
# TestRunSynthesisHappyPath
# ===========================================================================


class TestRunSynthesisHappyPath:
    def test_happy_path_returns_true_with_fields(self, tmp_path: Path, fake_api: _FakeApi) -> None:
        # Arrange
        out_path = tmp_path / "req-001.wav"
        wav_bytes = _make_silent_wav_bytes()

        def _behavior(text, *, engine, output, **kw):
            output.write_bytes(wav_bytes)

        fake_api._behavior = _behavior
        state = _make_state()
        payload: dict = {}

        # Act
        ok, result = _run(
            run_synthesis(state, payload, "req-001", "Hello world", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is True
        assert isinstance(result, dict)
        assert "audio_b64" in result
        assert result["mime_type"] == "audio/wav"
        assert isinstance(result["duration_ms"], int)
        # audio_b64 must be valid base64
        decoded = base64.b64decode(result["audio_b64"])
        assert decoded[:4] == b"RIFF"

    def test_happy_path_includes_waveform_b64_when_wav_readable(
        self, tmp_path: Path, fake_api: _FakeApi
    ) -> None:
        # Arrange — real WAV with enough frames to produce a waveform
        out_path = tmp_path / "req-wf.wav"
        wav_bytes = _make_silent_wav_bytes(n_frames=3200)

        def _behavior(text, *, engine, output, **kw):
            output.write_bytes(wav_bytes)

        fake_api._behavior = _behavior
        state = _make_state()

        # Act
        ok, result = _run(
            run_synthesis(state, {}, "req-wf", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is True
        assert isinstance(result, dict)
        assert "waveform_b64" in result
        # waveform_b64 must decode to exactly 256 bytes
        wf_bytes = base64.b64decode(result["waveform_b64"])
        assert len(wf_bytes) == 256

    def test_happy_path_omits_waveform_b64_when_wav_unreadable(
        self, tmp_path: Path, fake_api: _FakeApi
    ) -> None:
        # Arrange — 1-byte stub is not a valid WAV; wav_waveform_b64 returns None
        out_path = tmp_path / "req-stub.wav"

        def _behavior(text, *, engine, output, **kw):
            output.write_bytes(b"\x00")

        fake_api._behavior = _behavior
        state = _make_state()

        # Act
        ok, result = _run(
            run_synthesis(state, {}, "req-stub", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is True
        assert isinstance(result, dict)
        assert "waveform_b64" not in result

    def test_set_model_loaded_called_once_with_engine(
        self, tmp_path: Path, fake_api: _FakeApi
    ) -> None:
        # Arrange
        out_path = tmp_path / "req-ml.wav"
        recorded: list[str] = []

        def _behavior(text, *, engine, output, **kw):
            output.write_bytes(b"\x00")

        fake_api._behavior = _behavior
        state = TtsRunnerState(  # type: ignore[call-arg]
            executor=_SyncExecutor(),  # type: ignore[arg-type]
            set_model_loaded=lambda e: recorded.append(e),
        )

        # Act
        _run(run_synthesis(state, {}, "req-ml", "Hello", "qwen-fast", out_path, trace_id="t1"))

        # Assert
        assert recorded == ["qwen-fast"]


# ===========================================================================
# TestRunSynthesisFallbackLanguage
# ===========================================================================


class TestRunSynthesisFallbackLanguage:
    def test_fallback_language_used_on_first_param_error(self, tmp_path: Path, monkeypatch) -> None:
        # Arrange — first call raises ParamValidationError, second succeeds with fallback
        from voicecli.api import ParamValidationError

        out_path = tmp_path / "req-fb.wav"
        call_languages: list[str | None] = []

        def _fake_generate(text, *, engine, output, **kw):
            lang = kw.get("language")
            call_languages.append(lang)
            if lang == "en":
                raise ParamValidationError("unsupported language: en")
            output.write_bytes(b"\x00")

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = _make_state()
        payload = {"language": "en", "fallback_language": "fr"}

        # Act
        ok, result = _run(
            run_synthesis(state, payload, "req-fb", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is True
        assert call_languages == ["en", "fr"], f"expected [en, fr], got {call_languages}"
        assert isinstance(result, dict)

    def test_no_retry_when_fallback_equals_primary(self, tmp_path: Path, monkeypatch) -> None:
        # Arrange — fallback_language == language → no retry
        from voicecli.api import ParamValidationError

        out_path = tmp_path / "req-same.wav"
        calls = 0

        def _fake_generate(text, *, engine, output, **kw):
            nonlocal calls
            calls += 1
            raise ParamValidationError("unsupported language")

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = _make_state()
        payload = {"language": "en", "fallback_language": "en"}

        # Act
        ok, error_code = _run(
            run_synthesis(state, payload, "req-same", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is False
        assert error_code == "param_validation_failed"
        assert calls == 1

    def test_no_retry_when_fallback_missing(self, tmp_path: Path, monkeypatch) -> None:
        # Arrange — no fallback_language key → no retry
        from voicecli.api import ParamValidationError

        out_path = tmp_path / "req-nofb.wav"
        calls = 0

        def _fake_generate(text, *, engine, output, **kw):
            nonlocal calls
            calls += 1
            raise ParamValidationError("unsupported language")

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = _make_state()
        payload = {"language": "en"}  # no fallback_language

        # Act
        ok, error_code = _run(
            run_synthesis(state, payload, "req-nofb", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is False
        assert error_code == "param_validation_failed"
        assert calls == 1

    def test_both_primary_and_fallback_fail_returns_param_validation_failed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Arrange — both calls raise ParamValidationError
        from voicecli.api import ParamValidationError

        out_path = tmp_path / "req-fbfail.wav"
        call_languages: list[str | None] = []

        def _fake_generate(text, *, engine, output, **kw):
            lang = kw.get("language")
            call_languages.append(lang)
            raise ParamValidationError(f"unsupported: {lang}")

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = _make_state()
        payload = {"language": "zz", "fallback_language": "xx"}

        # Act
        ok, error_code = _run(
            run_synthesis(state, payload, "req-fbfail", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is False
        assert error_code == "param_validation_failed"
        assert call_languages == ["zz", "xx"]

    def test_fallback_call_uses_fallback_language_kwarg(self, tmp_path: Path, monkeypatch) -> None:
        # Arrange — verify that the second call receives language="fr" explicitly
        from voicecli.api import ParamValidationError

        out_path = tmp_path / "req-fbkw.wav"
        second_call_kwargs: dict = {}

        call_count = 0

        def _fake_generate(text, *, engine, output, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ParamValidationError("first fail")
            # second call — record kwargs
            second_call_kwargs.update(kw)
            output.write_bytes(b"\x00")

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = _make_state()
        payload = {"language": "en", "fallback_language": "fr"}

        # Act
        ok, result = _run(
            run_synthesis(state, payload, "req-fbkw", "Hi", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is True
        assert second_call_kwargs.get("language") == "fr"


# ===========================================================================
# TestRunSynthesisErrors
# ===========================================================================


class TestRunSynthesisErrors:
    def test_runtime_error_returns_synthesis_failed(self, tmp_path: Path, monkeypatch) -> None:
        # Arrange
        out_path = tmp_path / "req-rt.wav"

        def _fake_generate(text, *, engine, output, **kw):
            raise RuntimeError("GPU OOM")

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = _make_state()

        # Act
        ok, error_code = _run(
            run_synthesis(state, {}, "req-rt", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is False
        assert error_code == "synthesis_failed"

    def test_generic_exception_returns_synthesis_failed(self, tmp_path: Path, monkeypatch) -> None:
        # Arrange
        out_path = tmp_path / "req-exc.wav"

        def _fake_generate(text, *, engine, output, **kw):
            raise Exception("boom")  # noqa: TRY002

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = _make_state()

        # Act
        ok, error_code = _run(
            run_synthesis(state, {}, "req-exc", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is False
        assert error_code == "synthesis_failed"


# ===========================================================================
# TestRunSynthesisChunkedOutput
# ===========================================================================


class TestRunSynthesisChunkedOutput:
    def test_chunked_single_chunk_concatenated(self, tmp_path: Path, monkeypatch) -> None:
        # Arrange — engine writes {stem}_001.wav + {stem}.done but NOT {stem}.wav
        out_path = tmp_path / "req-c1.wav"
        wav_bytes = _make_silent_wav_bytes()

        def _fake_generate(text, *, engine, output, **kw):
            p = Path(output)
            chunk = p.parent / f"{p.stem}_001.wav"
            chunk.write_bytes(wav_bytes)
            p.with_suffix(".done").write_text("done\n")
            # Intentionally do NOT write p ({stem}.wav)

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = _make_state()

        # Act
        ok, result = _run(
            run_synthesis(state, {}, "req-c1", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is True
        assert isinstance(result, dict)
        decoded = base64.b64decode(result["audio_b64"])
        assert decoded[:4] == b"RIFF"
        # Chunk and done files must be cleaned up
        assert not (tmp_path / "req-c1_001.wav").exists()
        assert not (tmp_path / "req-c1.done").exists()

    def test_chunked_multiple_chunks_duration_reflects_all(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Arrange — two 100 ms chunks; total duration ≥ 200 ms
        out_path = tmp_path / "req-c2.wav"
        wav_bytes = _make_silent_wav_bytes(n_frames=2205)  # 100 ms @ 22050 Hz

        def _fake_generate(text, *, engine, output, **kw):
            p = Path(output)
            for i in (1, 2):
                chunk = p.parent / f"{p.stem}_{i:03d}.wav"
                chunk.write_bytes(wav_bytes)
            p.with_suffix(".done").write_text("done\n")

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = _make_state()

        # Act
        ok, result = _run(
            run_synthesis(state, {}, "req-c2", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is True
        assert result["duration_ms"] >= 200
        for i in (1, 2):
            assert not (tmp_path / f"req-c2_{i:03d}.wav").exists()
        assert not (tmp_path / "req-c2.done").exists()

    def test_non_chunked_output_path_used_directly(self, tmp_path: Path, monkeypatch) -> None:
        # Arrange — engine writes {stem}.wav directly; no .done → collect_chunked_output returns []
        out_path = tmp_path / "req-nc.wav"
        wav_bytes = _make_silent_wav_bytes()

        def _fake_generate(text, *, engine, output, **kw):
            Path(output).write_bytes(wav_bytes)

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = _make_state()

        # Act
        ok, result = _run(
            run_synthesis(state, {}, "req-nc", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is True
        decoded = base64.b64decode(result["audio_b64"])
        assert decoded[:4] == b"RIFF"


# ===========================================================================
# TestRunSynthesisKwargForwarding
# ===========================================================================


class TestRunSynthesisKwargForwarding:
    @pytest.mark.parametrize(
        "payload,expected_present,expected_absent",
        [
            # All OPTIONAL_KWARGS present → all forwarded
            (
                {
                    "language": "en",
                    "voice": "alice",
                    "speed": 1.1,
                    "exaggeration": 0.7,
                    "cfg_weight": 0.5,
                    "accent": "british",
                    "personality": "calm",
                    "emotion": "neutral",
                },
                {
                    "language": "en",
                    "voice": "alice",
                    "speed": 1.1,
                    "exaggeration": 0.7,
                    "cfg_weight": 0.5,
                    "accent": "british",
                    "personality": "calm",
                    "emotion": "neutral",
                },
                [],
            ),
            # Minimal payload → no optional kwargs forwarded
            (
                {},
                {},
                [
                    "language",
                    "voice",
                    "speed",
                    "exaggeration",
                    "cfg_weight",
                    "accent",
                    "personality",
                    "emotion",
                    "chunked",
                    "chunk_size",
                    "segment_gap",
                    "crossfade",
                ],
            ),
            # None values are skipped
            (
                {"language": None, "voice": "alice"},
                {"voice": "alice"},
                ["language"],
            ),
        ],
        ids=["all_optional_kwargs", "minimal_payload", "none_values_skipped"],
    )
    def test_optional_kwargs_forwarding(
        self,
        tmp_path: Path,
        monkeypatch,
        payload: dict,
        expected_present: dict,
        expected_absent: list,
    ) -> None:
        # Arrange
        out_path = tmp_path / "req-kw.wav"
        captured: dict = {}

        def _fake_generate(text, *, engine, output, **kw):
            captured.update(kw)
            output.write_bytes(b"\x00")

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = _make_state()

        # Act
        ok, _result = _run(
            run_synthesis(state, payload, "req-kw", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is True
        for k, v in expected_present.items():
            assert captured.get(k) == v, f"expected {k}={v!r} in generate kwargs"
        for k in expected_absent:
            assert k not in captured, f"{k} should not be forwarded when absent/None"

    @pytest.mark.parametrize(
        "payload,expected",
        [
            ({"chunked": True}, True),
            ({"chunked": False}, False),
            ({"chunked": 1}, True),  # truthy int → bool(1) == True
            ({"chunked": 0}, False),  # falsy int → bool(0) == False
        ],
        ids=["chunked_true", "chunked_false", "chunked_int_truthy", "chunked_int_falsy"],
    )
    def test_chunked_kwarg_cast_to_bool(
        self, tmp_path: Path, monkeypatch, payload: dict, expected: bool
    ) -> None:
        # Arrange
        out_path = tmp_path / "req-chunked.wav"
        captured: dict = {}

        def _fake_generate(text, *, engine, output, **kw):
            captured.update(kw)
            output.write_bytes(b"\x00")

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = _make_state()

        # Act
        ok, _result = _run(
            run_synthesis(state, payload, "req-chunked", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is True
        assert captured.get("chunked") is expected
        assert isinstance(captured.get("chunked"), bool)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("chunk_size", 500),
            ("segment_gap", 250),
            ("crossfade", 100),
        ],
    )
    def test_named_chunking_fields_forwarded(
        self, tmp_path: Path, monkeypatch, field: str, value: int
    ) -> None:
        # Arrange
        out_path = tmp_path / f"req-{field}.wav"
        captured: dict = {}

        def _fake_generate(text, *, engine, output, **kw):
            captured.update(kw)
            output.write_bytes(b"\x00")

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = _make_state()
        payload = {field: value}

        # Act
        ok, _result = _run(
            run_synthesis(state, payload, f"req-{field}", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is True
        assert captured.get(field) == value

    def test_engine_arg_forwarded(self, tmp_path: Path, monkeypatch) -> None:
        # Arrange
        out_path = tmp_path / "req-eng.wav"
        captured: dict = {}

        def _fake_generate(text, *, engine, output, **kw):
            captured["engine"] = engine
            output.write_bytes(b"\x00")

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = _make_state()

        # Act
        ok, _result = _run(
            run_synthesis(state, {}, "req-eng", "Hello", "chatterbox", out_path, trace_id="t1")
        )

        # Assert
        assert ok is True
        assert captured["engine"] == "chatterbox"

    def test_out_path_forwarded_as_output(self, tmp_path: Path, monkeypatch) -> None:
        # Arrange
        out_path = tmp_path / "req-out.wav"
        captured: dict = {}

        def _fake_generate(text, *, engine, output, **kw):
            captured["output"] = output
            output.write_bytes(b"\x00")

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = _make_state()

        # Act
        ok, _result = _run(
            run_synthesis(state, {}, "req-out", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is True
        assert captured["output"] == out_path


# ===========================================================================
# TestConstantSets
# ===========================================================================


class TestConstantSets:
    def test_optional_kwargs_contains_expected_fields(self) -> None:
        # Verify the constant matches the spec contract
        expected = {
            "language",
            "voice",
            "speed",
            "exaggeration",
            "cfg_weight",
            "accent",
            "personality",
            "emotion",
        }
        assert set(OPTIONAL_KWARGS) == expected

    def test_named_kwargs_contains_expected_fields(self) -> None:
        expected = {"chunked", "chunk_size", "segment_gap", "crossfade"}
        assert set(NAMED_KWARGS) == expected
