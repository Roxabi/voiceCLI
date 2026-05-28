"""Unit tests for voicecli.adapters.nats._transcribe_runner (V2 — BlobRef contract).

Lifecycle invariant (pinned by spec): the runner owns scoped-path creation and
writes audio bytes fetched from BlobStore.  The adapter wraps run_transcription
in try/finally and calls cleanup(scoped_path).  The runner NEVER calls cleanup()
itself.

V2 signature:
    run_transcription(state, default_model, blob_ref, request_id, scoped_dir,
                      overrides, *, trace_id)
    -> tuple[bool, dict | str, Path | None]

BlobStore.get is async; we inject a FakeBlobStore via monkeypatch on the
module-level import inside the runner.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from tests.nats._fakes import _BLOBSTORE_PATCH_PATH, SyncExecutor

from roxabi_blobs import BlobRef
from voicecli.runtime.transcribe import Segment, TranscriptionResult

from voicecli.adapters.nats._transcribe_runner import (
    SttRunnerState,
    run_transcription,
)

# ---------------------------------------------------------------------------
# BlobRef factory
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)


def _make_blob_ref(
    *,
    store_key: str = "sha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    mime: str = "audio/wav",
    size: int = 32,
    source: str = "test",
    content_hash: str = "deadbeef",
) -> BlobRef:
    return BlobRef(
        store_key=store_key,
        mime=mime,
        size=size,
        source=source,
        content_hash=content_hash,
        created_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Fake BlobStore
# ---------------------------------------------------------------------------


class _FakeBlobStore:
    """Async BlobStore that returns pre-loaded bytes for any store_key."""

    def __init__(self, data: bytes = b"\x00" * 32) -> None:
        self._data = data
        self.get_calls: list[str] = []

    async def get(self, store_key: str) -> bytes:
        self.get_calls.append(store_key)
        return self._data


class _RaisingBlobStore:
    """BlobStore whose get() always raises."""

    async def get(self, store_key: str) -> bytes:
        raise RuntimeError("blobstore unreachable")


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
        return TranscriptionResult(
            text="hello", language="en", segments=[Segment(end=1.5, start=0.0, text="")]
        )

    def warmup_model(self, model: str) -> None:
        self.warmup_calls.append(model)
        if self._warmup_behavior is not None:
            self._warmup_behavior(model)


def _make_state(
    *,
    model_warm: bool = False,
    set_model_warm: Callable[[bool], None] | None = None,
    set_model_loaded: Callable[[str | None], None] | None = None,
) -> SttRunnerState:
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
    state._warm_calls = warm_calls  # type: ignore[attr-defined]
    state._loaded_calls = loaded_calls  # type: ignore[attr-defined]
    return state


def _run(coro):
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
    monkeypatch.setattr("voicecli.api.transcribe", fake.transcribe)
    monkeypatch.setattr("voicecli.api.warmup_model", fake.warmup_model)
    return fake


@pytest.fixture
def fake_blobstore(monkeypatch):  # pyright: ignore[reportUnusedFunction]
    """Inject a FakeBlobStore into the runner via the blobs module singleton.

    The runner does ``from voicecli.adapters.nats.blobs import get_blobstore``
    inside the function body — a fresh import each call that goes through the
    module cache.  Patching `blobs.get_blobstore` (the attribute on the cached
    module object) is therefore the correct intercept point.
    """
    store = _FakeBlobStore()
    monkeypatch.setattr(
        _BLOBSTORE_PATCH_PATH,
        lambda: store,
    )
    return store


# ===========================================================================
# TestRunTranscriptionHappyPath
# ===========================================================================


class TestRunTranscriptionHappyPath:
    def test_happy_path_returns_true_with_fields(
        self, tmp_path: Path, fake_api: _FakeApi, fake_blobstore: _FakeBlobStore
    ) -> None:
        # Arrange
        state = _make_state()
        blob_ref = _make_blob_ref()

        # Act
        ok, result, scoped_path = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                blob_ref,
                "req-001",
                tmp_path,
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
        assert scoped_path is not None

    def test_happy_path_calls_set_model_warm_and_set_model_loaded(
        self, tmp_path: Path, fake_api: _FakeApi, fake_blobstore: _FakeBlobStore
    ) -> None:
        # Arrange
        warm_calls: list[bool] = []
        loaded_calls: list[str | None] = []
        state = _make_state(
            set_model_warm=lambda v: warm_calls.append(v),
            set_model_loaded=lambda m: loaded_calls.append(m),
        )

        # Act
        ok, result, _ = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                _make_blob_ref(),
                "req-002",
                tmp_path,
                {},
                trace_id="t2",
            )
        )

        # Assert
        assert ok is True
        assert warm_calls == [True], f"expected set_model_warm(True) once, got {warm_calls}"
        assert loaded_calls == ["large-v3-turbo"]

    def test_warm_model_skips_warmup_on_second_call(
        self, tmp_path: Path, fake_api: _FakeApi, fake_blobstore: _FakeBlobStore
    ) -> None:
        # Arrange — model already warm; warmup_model must NOT be called
        state = _make_state(model_warm=True)

        # Act
        ok, result, _ = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                _make_blob_ref(),
                "req-003",
                tmp_path,
                {},
                trace_id="t3",
            )
        )

        # Assert
        assert ok is True
        assert fake_api.warmup_calls == [], "warmup_model must not be called when already warm"

    def test_blob_ref_store_key_fetched_from_blobstore(
        self, tmp_path: Path, fake_api: _FakeApi, fake_blobstore: _FakeBlobStore
    ) -> None:
        """Runner calls BlobStore.get(blob_ref.store_key)."""
        # Arrange
        state = _make_state(model_warm=True)
        blob_ref = _make_blob_ref(
            store_key="sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        )

        # Act
        ok, _, _ = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                blob_ref,
                "req-store-key",
                tmp_path,
                {},
                trace_id="t-sk",
            )
        )

        # Assert — BlobStore.get called with the blob_ref store_key
        assert ok is True
        assert fake_blobstore.get_calls == [
            "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        ]

    def test_scoped_path_returned_on_success(
        self, tmp_path: Path, fake_api: _FakeApi, fake_blobstore: _FakeBlobStore
    ) -> None:
        """Third tuple element is a Path the adapter uses for cleanup."""
        # Arrange
        state = _make_state(model_warm=True)
        blob_ref = _make_blob_ref(mime="audio/wav")

        # Act
        ok, _, scoped_path = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                blob_ref,
                "req-path",
                tmp_path,
                {},
                trace_id="t-path",
            )
        )

        # Assert
        assert ok is True
        assert scoped_path is not None
        assert isinstance(scoped_path, Path)
        assert scoped_path.parent == tmp_path
        assert scoped_path.name.startswith("req-path")

    @pytest.mark.parametrize(
        "mime,expected_ext",
        [
            ("audio/wav", "wav"),
            ("audio/mp3", "mp3"),
        ],
        ids=["wav", "mp3"],
    )
    def test_mime_from_blob_ref_determines_file_extension(
        self,
        tmp_path: Path,
        fake_api: _FakeApi,
        fake_blobstore: _FakeBlobStore,
        mime: str,
        expected_ext: str,
    ) -> None:
        # Arrange
        state = _make_state(model_warm=True)
        blob_ref = _make_blob_ref(mime=mime)

        # Act
        ok, _, scoped_path = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                blob_ref,
                "req-mime",
                tmp_path,
                {},
                trace_id="t-mime",
            )
        )

        # Assert
        assert ok is True
        assert scoped_path is not None
        assert scoped_path.suffix == f".{expected_ext}"


# ===========================================================================
# TestRunTranscriptionBlobStoreFailure
# ===========================================================================


class TestRunTranscriptionBlobStoreFailure:
    def test_blobstore_get_raises_returns_audio_fetch_failed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """BlobStore.get() raises → runner returns (False, 'audio_fetch_failed', None).

        Negative-test: deleting the try/except around get_blobstore().get() would
        let the exception propagate instead of returning the error tuple — test fails.
        """
        # Arrange
        monkeypatch.setattr(
            _BLOBSTORE_PATCH_PATH,
            lambda: _RaisingBlobStore(),
        )
        state = _make_state(model_warm=True)
        blob_ref = _make_blob_ref()

        # Act
        ok, error_code, scoped_path = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                blob_ref,
                "req-blobfail",
                tmp_path,
                {},
                trace_id="t-fail",
            )
        )

        # Assert
        assert ok is False
        assert error_code == "audio_fetch_failed"
        assert scoped_path is None

    def test_blobstore_get_failure_does_not_call_transcribe(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Arrange
        monkeypatch.setattr(
            _BLOBSTORE_PATCH_PATH,
            lambda: _RaisingBlobStore(),
        )
        transcribe_calls: list = []
        monkeypatch.setattr(
            "voicecli.api.transcribe",
            lambda *a, **kw: transcribe_calls.append((a, kw)),
        )
        state = _make_state(model_warm=True)

        # Act
        ok, error_code, _ = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                _make_blob_ref(),
                "req-blobfail-notrans",
                tmp_path,
                {},
                trace_id="t-fn",
            )
        )

        # Assert
        assert ok is False
        assert error_code == "audio_fetch_failed"
        assert transcribe_calls == [], "api.transcribe must not be called on BlobStore failure"

    def test_blobstore_config_error_logs_blobstore_init_failed(
        self, tmp_path: Path, monkeypatch, caplog
    ) -> None:
        """SC-test-4: a raising backend factory (BlobstoreConfigError) →
        runner logs ``blobstore_init_failed`` and returns
        ``(False, 'blobstore_not_configured', None)``.

        This distinguishes a misconfigured satellite (env var missing, ADR-068
        violation) from a transient network/storage failure
        (``blobstore_get_failed`` / ``audio_fetch_failed``).
        """
        # Arrange — make get_blobstore() raise BlobstoreConfigError on call
        from voicecli.adapters.nats import blobs

        def _raising_factory():
            raise blobs.BlobstoreConfigError("BLOBSTORE_URL not set")

        monkeypatch.setattr(_BLOBSTORE_PATCH_PATH, _raising_factory)
        state = _make_state(model_warm=True)

        # Act
        with caplog.at_level("ERROR"):
            ok, error_code, scoped_path = _run(
                run_transcription(
                    state,
                    "large-v3-turbo",
                    _make_blob_ref(),
                    "req-cfgfail",
                    tmp_path,
                    {},
                    trace_id="t-cfg",
                )
            )

        # Assert — structured error code, scoped_path None, log emitted
        assert ok is False
        assert error_code == "blobstore_not_configured"
        assert scoped_path is None
        assert any("blobstore_init_failed" in r.message for r in caplog.records), (
            f"expected 'blobstore_init_failed' log record; got {[r.message for r in caplog.records]}"
        )


# ===========================================================================
# TestRunTranscriptionModelLoad
# ===========================================================================


class TestRunTranscriptionModelLoad:
    def test_warmup_raises_returns_model_load_failed(
        self, tmp_path: Path, fake_blobstore: _FakeBlobStore, monkeypatch
    ) -> None:
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
            return TranscriptionResult(text="x", language="en", segments=[])

        monkeypatch.setattr("voicecli.api.warmup_model", _fail_warmup)
        monkeypatch.setattr("voicecli.api.transcribe", _fake_transcribe)

        # Act
        ok, error_code, _ = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                _make_blob_ref(),
                "req-warmfail",
                tmp_path,
                {},
                trace_id="t-wf",
            )
        )

        # Assert
        assert ok is False
        assert error_code == "model_load_failed"
        assert True not in warm_calls, "set_model_warm(True) must not fire on warmup failure"
        assert not loaded_calls
        assert transcribe_calls == []

    def test_two_consecutive_failing_warmups_attempt_warmup_twice(
        self, tmp_path: Path, fake_blobstore: _FakeBlobStore, monkeypatch
    ) -> None:
        # Arrange — no backoff on failure; each call re-attempts warmup
        warmup_attempt_count = 0

        def _fail_warmup(model: str) -> None:
            nonlocal warmup_attempt_count
            warmup_attempt_count += 1
            raise RuntimeError("still failing")

        monkeypatch.setattr("voicecli.api.warmup_model", _fail_warmup)

        state = _make_state()

        # Act
        _run(
            run_transcription(
                state,
                "large-v3-turbo",
                _make_blob_ref(),
                "req-wf-retry1",
                tmp_path,
                {},
                trace_id="t1",
            )
        )
        _run(
            run_transcription(
                state,
                "large-v3-turbo",
                _make_blob_ref(),
                "req-wf-retry2",
                tmp_path,
                {},
                trace_id="t2",
            )
        )

        # Assert
        assert warmup_attempt_count == 2


# ===========================================================================
# TestRunTranscriptionParamValidation
# ===========================================================================


class TestRunTranscriptionParamValidation:
    def test_param_validation_error_returns_param_validation_failed(
        self, tmp_path: Path, fake_blobstore: _FakeBlobStore, monkeypatch
    ) -> None:
        # Arrange
        from voicecli.api import ParamValidationError

        def _bad_transcribe(out_path, *, model, _skip_daemon=True, **kw):
            raise ParamValidationError("bad lang")

        monkeypatch.setattr("voicecli.api.warmup_model", lambda m: None)
        monkeypatch.setattr("voicecli.api.transcribe", _bad_transcribe)

        state = _make_state()

        # Act
        ok, error_code, _ = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                _make_blob_ref(),
                "req-val",
                tmp_path,
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
        self, tmp_path: Path, fake_blobstore: _FakeBlobStore, monkeypatch
    ) -> None:
        # Arrange
        def _crash_transcribe(out_path, *, model, _skip_daemon=True, **kw):
            raise RuntimeError("model OOM during inference")

        monkeypatch.setattr("voicecli.api.warmup_model", lambda m: None)
        monkeypatch.setattr("voicecli.api.transcribe", _crash_transcribe)

        state = _make_state()

        # Act
        ok, error_code, _ = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                _make_blob_ref(),
                "req-crash",
                tmp_path,
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
        self, tmp_path: Path, fake_blobstore: _FakeBlobStore, monkeypatch
    ) -> None:
        # Arrange
        captured: dict = {}

        def _capturing_transcribe(out_path, *, model, _skip_daemon=True, **kw):
            captured.update(kw)
            return TranscriptionResult(
                text="bonjour", language="fr", segments=[Segment(end=1.0, start=0.0, text="")]
            )

        monkeypatch.setattr("voicecli.api.warmup_model", lambda m: None)
        monkeypatch.setattr("voicecli.api.transcribe", _capturing_transcribe)

        state = _make_state()
        overrides = {"language": "fr"}

        # Act
        ok, result, _ = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                _make_blob_ref(),
                "req-fr",
                tmp_path,
                overrides,
                trace_id="t-fr",
            )
        )

        # Assert
        assert ok is True
        assert captured.get("language") == "fr"

    def test_empty_overrides_no_extra_kwargs(
        self, tmp_path: Path, fake_blobstore: _FakeBlobStore, monkeypatch
    ) -> None:
        # Arrange
        captured: dict = {}

        def _capturing_transcribe(out_path, *, model, _skip_daemon=True, **kw):
            captured.update(kw)
            return TranscriptionResult(
                text="hi", language="en", segments=[Segment(end=0.5, start=0.0, text="")]
            )

        monkeypatch.setattr("voicecli.api.warmup_model", lambda m: None)
        monkeypatch.setattr("voicecli.api.transcribe", _capturing_transcribe)

        state = _make_state()

        # Act
        ok, _, _ = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                _make_blob_ref(),
                "req-empty-ov",
                tmp_path,
                {},
                trace_id="t-empty",
            )
        )

        # Assert
        assert ok is True
        for key in ("language", "task", "initial_prompt", "language_fallback"):
            assert key not in captured

    def test_multiple_overrides_forwarded_together(
        self, tmp_path: Path, fake_blobstore: _FakeBlobStore, monkeypatch
    ) -> None:
        # Arrange
        captured: dict = {}

        def _capturing_transcribe(out_path, *, model, _skip_daemon=True, **kw):
            captured.update(kw)
            return TranscriptionResult(
                text="hallo", language="de", segments=[Segment(end=2.0, start=0.0, text="")]
            )

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
        ok, result, _ = _run(
            run_transcription(
                state,
                "large-v3-turbo",
                _make_blob_ref(),
                "req-multi-ov",
                tmp_path,
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
