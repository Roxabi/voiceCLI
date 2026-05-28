"""Unit tests for voicecli.adapters.nats._synthesize_runner (issue #147, updated #144).

T17: Updated for V2 BlobRef contract — mocks get_blobstore().put returning a BlobRef;
asserts TtsResponse carries blob_ref.

Note on mock design: roxabi_blobs.BlobRef.model_dump() includes 'id' and 'is_sentinel'
which are forbidden by roxabi_contracts.BlobRef (extra="forbid"). The fake returned by
the mock's put() therefore produces only contract-compatible fields from model_dump() —
this is the correct test-time bridge (mirrors the intended production bridge:
roxabi_contracts.BlobRef.model_validate(roxabi_blobs_ref.model_dump(exclude={"id","is_sentinel"}))).
"""

from __future__ import annotations

import asyncio
import base64
import io
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from tests.nats._fakes import _BLOBSTORE_PATCH_PATH, _FakeBlobRef, SyncExecutor

from roxabi_contracts.errors import WorkerError

from voicecli.adapters.nats._synthesize_runner import (
    NAMED_KWARGS,
    OPTIONAL_KWARGS,
    TtsRunnerState,
    run_synthesis,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


class _FakeBlobStore:
    """Async-compatible fake BlobStore that records put() calls."""

    def __init__(
        self,
        *,
        raise_on_put: Exception | None = None,
        blob_ref: _FakeBlobRef | None = None,
    ) -> None:
        self.put_calls: list[dict] = []
        self._raise_on_put = raise_on_put
        self._blob_ref = blob_ref or _FakeBlobRef(
            store_key="sha256:abc123abc123abc123abc123abc123abc123abc123abc123abc123abc123abc1",
            content_hash="abc123",
        )

    async def put(
        self,
        data: bytes,
        *,
        mime: str,
        source: str,
        filename: str | None = None,
        platform_ref: str | None = None,
        platform_message_id: str | None = None,
    ) -> _FakeBlobRef:
        self.put_calls.append({"data": data, "mime": mime, "source": source})
        if self._raise_on_put is not None:
            raise self._raise_on_put
        return self._blob_ref


def _make_state(*, set_model_loaded=None) -> "TtsRunnerState":
    recorded: list[str] = []
    cb = set_model_loaded or (lambda e: recorded.append(e))
    return TtsRunnerState(executor=SyncExecutor(), set_model_loaded=cb)  # type: ignore[arg-type]


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
def fake_api(monkeypatch):  # pyright: ignore[reportUnusedFunction]
    fake = _FakeApi()
    monkeypatch.setattr("voicecli.api.generate", fake.generate)
    return fake


@pytest.fixture
def fake_blobstore(monkeypatch):  # pyright: ignore[reportUnusedFunction]
    """Inject a _FakeBlobStore into the runner via get_blobstore().

    The runner imports get_blobstore via a deferred 'from ... import' inside the
    function body, so we patch the source module (blobs.get_blobstore) rather than
    a module-level attribute on _synthesize_runner.
    """
    store = _FakeBlobStore()
    monkeypatch.setattr(
        _BLOBSTORE_PATCH_PATH,
        lambda: store,
    )
    return store


# ===========================================================================
# TestRunSynthesisHappyPath
# ===========================================================================


class TestRunSynthesisHappyPath:
    def test_happy_path_returns_true_with_blob_ref(
        self, tmp_path: Path, fake_api: _FakeApi, fake_blobstore: _FakeBlobStore
    ) -> None:
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
        assert "blob_ref" in result
        assert result["mime_type"] == "audio/wav"
        assert isinstance(result["duration_ms"], int)
        # blob_ref must carry the expected store_key from the fake
        assert (
            result["blob_ref"]["store_key"]
            == "sha256:abc123abc123abc123abc123abc123abc123abc123abc123abc123abc123abc1"
        )

    def test_happy_path_calls_blobstore_put_with_correct_kwargs(
        self, tmp_path: Path, fake_api: _FakeApi, fake_blobstore: _FakeBlobStore
    ) -> None:
        # Arrange — verify put() is called with mime="audio/wav" and source="voicecli"
        out_path = tmp_path / "req-put.wav"
        wav_bytes = _make_silent_wav_bytes()

        def _behavior(text, *, engine, output, **kw):
            output.write_bytes(wav_bytes)

        fake_api._behavior = _behavior
        state = _make_state()

        # Act
        ok, result = _run(
            run_synthesis(state, {}, "req-put", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is True
        assert len(fake_blobstore.put_calls) == 1
        call = fake_blobstore.put_calls[0]
        assert call["mime"] == "audio/wav"
        assert call["source"] == "voicecli"
        # put() receives the actual WAV bytes
        assert call["data"][:4] == b"RIFF"

    def test_happy_path_includes_waveform_b64_when_wav_readable(
        self, tmp_path: Path, fake_api: _FakeApi, fake_blobstore: _FakeBlobStore
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
        self, tmp_path: Path, fake_api: _FakeApi, fake_blobstore: _FakeBlobStore
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
        self, tmp_path: Path, fake_api: _FakeApi, fake_blobstore: _FakeBlobStore
    ) -> None:
        # Arrange
        out_path = tmp_path / "req-ml.wav"
        recorded: list[str] = []

        def _behavior(text, *, engine, output, **kw):
            output.write_bytes(b"\x00")

        fake_api._behavior = _behavior
        state = TtsRunnerState(  # type: ignore[call-arg]
            executor=SyncExecutor(),  # type: ignore[arg-type]
            set_model_loaded=lambda e: recorded.append(e),
        )

        # Act
        _run(run_synthesis(state, {}, "req-ml", "Hello", "qwen-fast", out_path, trace_id="t1"))

        # Assert
        assert recorded == ["qwen-fast"]


# ===========================================================================
# TestRunSynthesisBlobStorePutFailure — SC-test-4 parity for TTS
# ===========================================================================


class TestRunSynthesisBlobStorePutFailure:
    def test_put_raises_returns_audio_store_failed(
        self, tmp_path: Path, fake_api: _FakeApi, monkeypatch
    ) -> None:
        """BlobStore.put raises → runner returns (False, WorkerError(code="audio_store_failed")).

        Negative-test: if the try/except around get_blobstore().put() is removed,
        the exception propagates to the outer except and yields "synthesis_failed" —
        not "audio_store_failed". This test fails if the specific error code is gone.
        """
        # Arrange
        store = _FakeBlobStore(raise_on_put=ConnectionError("blobstore unreachable"))
        monkeypatch.setattr(
            _BLOBSTORE_PATCH_PATH,
            lambda: store,
        )
        out_path = tmp_path / "req-storefail.wav"
        wav_bytes = _make_silent_wav_bytes()

        def _behavior(text, *, engine, output, **kw):
            output.write_bytes(wav_bytes)

        fake_api._behavior = _behavior
        state = _make_state()

        # Act
        ok, worker_error = _run(
            run_synthesis(state, {}, "req-storefail", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is False
        assert isinstance(worker_error, WorkerError)
        assert worker_error.code == "audio_store_failed"

    def test_put_raises_after_engine_success_not_synthesis_failed(
        self, tmp_path: Path, fake_api: _FakeApi, monkeypatch
    ) -> None:
        """Error code is 'audio_store_failed', NOT 'synthesis_failed'.

        Distinguishes between engine failure (synthesis_failed) and blob upload failure
        (audio_store_failed). If the inner guard were deleted, the outer catch would
        return 'synthesis_failed' instead.
        """
        # Arrange — engine succeeds, then put raises
        store = _FakeBlobStore(raise_on_put=RuntimeError("network timeout"))
        monkeypatch.setattr(
            _BLOBSTORE_PATCH_PATH,
            lambda: store,
        )
        out_path = tmp_path / "req-storefail2.wav"
        wav_bytes = _make_silent_wav_bytes()

        def _behavior(text, *, engine, output, **kw):
            output.write_bytes(wav_bytes)

        fake_api._behavior = _behavior
        state = _make_state()

        # Act
        ok, worker_error = _run(
            run_synthesis(state, {}, "req-storefail2", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert — must be audio_store_failed, not synthesis_failed
        assert ok is False
        assert isinstance(worker_error, WorkerError)
        assert worker_error.code == "audio_store_failed", (
            f"expected 'audio_store_failed' but got {worker_error.code!r}; "
            "deleting the inner blobstore guard would yield 'synthesis_failed'"
        )

    def test_blobstore_config_error_logs_blobstore_init_failed(
        self, tmp_path: Path, fake_api: _FakeApi, monkeypatch, caplog
    ) -> None:
        """SC-test-4 (TTS side): a raising backend factory
        (``BlobstoreConfigError``) → runner logs ``blobstore_init_failed`` and
        returns ``(False, 'blobstore_not_configured')``.

        Symmetric with the STT runner test. Distinguishes a misconfigured
        satellite (ADR-068 violation, missing env var) from a transient PUT
        failure (``audio_store_failed``).
        """
        # Arrange — engine succeeds (writes the WAV), then get_blobstore() raises
        from voicecli.adapters.nats import blobs

        def _raising_factory():
            raise blobs.BlobstoreConfigError("BLOBSTORE_BEARER_TOKEN not set")

        monkeypatch.setattr(_BLOBSTORE_PATCH_PATH, _raising_factory)
        out_path = tmp_path / "req-cfgfail.wav"
        wav_bytes = _make_silent_wav_bytes()

        def _behavior(text, *, engine, output, **kw):
            output.write_bytes(wav_bytes)

        fake_api._behavior = _behavior
        state = _make_state()

        # Act
        with caplog.at_level("ERROR"):
            ok, worker_error = _run(
                run_synthesis(state, {}, "req-cfgfail", "Hello", "mock", out_path, trace_id="t-cfg")
            )

        # Assert — structured error code + log emitted
        assert ok is False
        assert isinstance(worker_error, WorkerError)
        assert worker_error.code == "blobstore_not_configured"
        assert any("blobstore_init_failed" in r.message for r in caplog.records), (
            f"expected 'blobstore_init_failed' log; got {[r.message for r in caplog.records]}"
        )

    def test_put_raises_does_not_call_put_twice(
        self, tmp_path: Path, fake_api: _FakeApi, monkeypatch
    ) -> None:
        """put() is called exactly once even when it raises."""
        # Arrange
        store = _FakeBlobStore(raise_on_put=OSError("disk full"))
        monkeypatch.setattr(
            _BLOBSTORE_PATCH_PATH,
            lambda: store,
        )
        out_path = tmp_path / "req-oncefail.wav"

        def _behavior(text, *, engine, output, **kw):
            output.write_bytes(b"\x00")

        fake_api._behavior = _behavior
        state = _make_state()

        # Act
        _run(run_synthesis(state, {}, "req-oncefail", "Hello", "mock", out_path, trace_id="t1"))

        # Assert — put was called exactly once
        assert len(store.put_calls) == 1


# ===========================================================================
# TestRunSynthesisFallbackLanguage
# ===========================================================================


class TestRunSynthesisFallbackLanguage:
    def test_fallback_language_used_on_first_param_error(
        self, tmp_path: Path, monkeypatch, fake_blobstore: _FakeBlobStore
    ) -> None:
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

    def test_no_retry_when_fallback_equals_primary(
        self, tmp_path: Path, monkeypatch, fake_blobstore: _FakeBlobStore
    ) -> None:
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
        ok, worker_error = _run(
            run_synthesis(state, payload, "req-same", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is False
        assert isinstance(worker_error, WorkerError)
        assert worker_error.code == "param_validation_failed"
        assert calls == 1

    def test_no_retry_when_fallback_missing(
        self, tmp_path: Path, monkeypatch, fake_blobstore: _FakeBlobStore
    ) -> None:
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
        ok, worker_error = _run(
            run_synthesis(state, payload, "req-nofb", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is False
        assert isinstance(worker_error, WorkerError)
        assert worker_error.code == "param_validation_failed"
        assert calls == 1

    def test_both_primary_and_fallback_fail_returns_param_validation_failed(
        self, tmp_path: Path, monkeypatch, fake_blobstore: _FakeBlobStore
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
        ok, worker_error = _run(
            run_synthesis(state, payload, "req-fbfail", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is False
        assert isinstance(worker_error, WorkerError)
        assert worker_error.code == "param_validation_failed"
        assert call_languages == ["zz", "xx"]

    def test_fallback_call_uses_fallback_language_kwarg(
        self, tmp_path: Path, monkeypatch, fake_blobstore: _FakeBlobStore
    ) -> None:
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
    def test_runtime_error_returns_synthesis_failed(
        self, tmp_path: Path, monkeypatch, fake_blobstore: _FakeBlobStore
    ) -> None:
        # Arrange
        out_path = tmp_path / "req-rt.wav"
        recorded: list[str] = []

        def _fake_generate(text, *, engine, output, **kw):
            raise RuntimeError("GPU OOM")

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = TtsRunnerState(  # type: ignore[call-arg]
            executor=SyncExecutor(),  # type: ignore[arg-type]
            set_model_loaded=lambda e: recorded.append(e),
        )

        # Act
        ok, worker_error = _run(
            run_synthesis(state, {}, "req-rt", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is False
        assert isinstance(worker_error, WorkerError)
        assert worker_error.code == "synthesis_failed"
        assert worker_error.retryable is False
        assert worker_error.message == "RuntimeError"
        # set_model_loaded fires BEFORE synthesis (intentional design — pinned here so a
        # refactor that moves the call after-success would not silently pass this test).
        assert recorded == ["mock"], (
            f"expected set_model_loaded(engine) called once before failure, got {recorded}"
        )

    def test_generic_exception_returns_synthesis_failed(
        self, tmp_path: Path, monkeypatch, fake_blobstore: _FakeBlobStore
    ) -> None:
        # Arrange
        out_path = tmp_path / "req-exc.wav"
        recorded: list[str] = []

        def _fake_generate(text, *, engine, output, **kw):
            raise Exception("boom")  # noqa: TRY002

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = TtsRunnerState(  # type: ignore[call-arg]
            executor=SyncExecutor(),  # type: ignore[arg-type]
            set_model_loaded=lambda e: recorded.append(e),
        )

        # Act
        ok, worker_error = _run(
            run_synthesis(state, {}, "req-exc", "Hello", "mock", out_path, trace_id="t1")
        )

        # Assert
        assert ok is False
        assert isinstance(worker_error, WorkerError)
        assert worker_error.code == "synthesis_failed"
        assert worker_error.retryable is False
        # set_model_loaded fires BEFORE synthesis (intentional design — pinned here so a
        # refactor that moves the call after-success would not silently pass this test).
        assert recorded == ["mock"], (
            f"expected set_model_loaded(engine) called once before failure, got {recorded}"
        )


# ===========================================================================
# TestRunSynthesisChunkedOutput
# ===========================================================================


class TestRunSynthesisChunkedOutput:
    def test_chunked_single_chunk_concatenated(
        self, tmp_path: Path, monkeypatch, fake_blobstore: _FakeBlobStore
    ) -> None:
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
        assert "blob_ref" in result
        # Chunk and done files must be cleaned up
        assert not (tmp_path / "req-c1_001.wav").exists()
        assert not (tmp_path / "req-c1.done").exists()

    def test_chunked_multiple_chunks_duration_reflects_all(
        self, tmp_path: Path, monkeypatch, fake_blobstore: _FakeBlobStore
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
        assert isinstance(result, dict)
        assert result["duration_ms"] >= 200
        for i in (1, 2):
            assert not (tmp_path / f"req-c2_{i:03d}.wav").exists()
        assert not (tmp_path / "req-c2.done").exists()

    def test_non_chunked_output_path_used_directly(
        self, tmp_path: Path, monkeypatch, fake_blobstore: _FakeBlobStore
    ) -> None:
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
        assert isinstance(result, dict)
        assert "blob_ref" in result


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
        fake_blobstore: _FakeBlobStore,
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
        self,
        tmp_path: Path,
        monkeypatch,
        payload: dict,
        expected: bool,
        fake_blobstore: _FakeBlobStore,
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
        self,
        tmp_path: Path,
        monkeypatch,
        field: str,
        value: int,
        fake_blobstore: _FakeBlobStore,
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

    def test_engine_arg_forwarded(
        self, tmp_path: Path, monkeypatch, fake_blobstore: _FakeBlobStore
    ) -> None:
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

    def test_out_path_forwarded_as_output(
        self, tmp_path: Path, monkeypatch, fake_blobstore: _FakeBlobStore
    ) -> None:
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


# ===========================================================================
# TestRunSynthesisWorkerError — structured WorkerError on failure paths
# ===========================================================================


class TestRunSynthesisWorkerError:
    def test_unknown_voice_returns_structured_worker_error(
        self, tmp_path: Path, monkeypatch, fake_blobstore: _FakeBlobStore
    ) -> None:
        """ValueError('Unknown voice ...') → (False, WorkerError(code='unknown_voice')).

        Verifies: code='unknown_voice', retryable=False, message contains the voice
        name, and detail carries the full exception text (incl. available list).
        """
        out_path = tmp_path / "req-uvce.wav"

        def _fake_generate(text, *, engine, output, **kw):
            raise ValueError("Unknown voice 'Cherry'. Available: ['Alice', 'Bob']")

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = _make_state()

        ok, worker_error = _run(
            run_synthesis(state, {}, "req-uvce", "Hello", "mock", out_path, trace_id="t1")
        )

        assert ok is False
        assert isinstance(worker_error, WorkerError)
        assert worker_error.code == "unknown_voice"
        assert worker_error.retryable is False
        assert "Cherry" in worker_error.message
        assert worker_error.detail is not None
        assert "Available" in worker_error.detail

    def test_unknown_voice_does_not_escape_run_synthesis(
        self, tmp_path: Path, monkeypatch, fake_blobstore: _FakeBlobStore
    ) -> None:
        """run_synthesis must never raise — unknown voice is caught and returned."""
        out_path = tmp_path / "req-uvne.wav"

        def _fake_generate(text, *, engine, output, **kw):
            raise ValueError("Unknown voice 'Ghost'. Available: ['Alice']")

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = _make_state()

        # Must not raise; must return (False, WorkerError)
        result = _run(
            run_synthesis(state, {}, "req-uvne", "Hello", "mock", out_path, trace_id="t1")
        )
        assert result[0] is False
        assert isinstance(result[1], WorkerError)

    def test_non_unknown_voice_value_error_returns_synthesis_failed(
        self, tmp_path: Path, monkeypatch, fake_blobstore: _FakeBlobStore
    ) -> None:
        """A ValueError not starting with 'Unknown voice' → synthesis_failed, not unknown_voice."""
        out_path = tmp_path / "req-veouf.wav"

        def _fake_generate(text, *, engine, output, **kw):
            raise ValueError("some other problem")

        monkeypatch.setattr("voicecli.api.generate", _fake_generate)
        state = _make_state()

        ok, worker_error = _run(
            run_synthesis(state, {}, "req-veouf", "Hello", "mock", out_path, trace_id="t1")
        )

        assert ok is False
        assert isinstance(worker_error, WorkerError)
        assert worker_error.code == "synthesis_failed"
        assert worker_error.retryable is False

    def test_all_failure_paths_return_worker_error_instances(
        self, tmp_path: Path, monkeypatch, fake_blobstore: _FakeBlobStore
    ) -> None:
        """Smoke-test: every failure code from run_synthesis is a WorkerError, never a str."""
        from voicecli.api import ParamValidationError

        cases: list[tuple[str, Exception]] = [
            ("req-pve", ParamValidationError("bad param")),
            ("req-rte", RuntimeError("OOM")),
            ("req-uve", ValueError("Unknown voice 'X'. Available: []")),
            ("req-vge", ValueError("generic value error")),
        ]

        for req_id, exc_to_raise in cases:
            out_path = tmp_path / f"{req_id}.wav"

            def _fake_generate(text, *, engine, output, exc=exc_to_raise, **kw):
                raise exc

            monkeypatch.setattr("voicecli.api.generate", _fake_generate)
            state = _make_state()
            ok, result = _run(
                run_synthesis(state, {}, req_id, "Hello", "mock", out_path, trace_id="t1")
            )
            assert ok is False, f"{req_id}: expected ok=False"
            assert isinstance(result, WorkerError), (
                f"{req_id}: expected WorkerError, got {type(result).__name__!r} ({result!r})"
            )
