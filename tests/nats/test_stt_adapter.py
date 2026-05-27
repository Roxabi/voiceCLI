"""Tests for SttNatsAdapter (V2 — BlobRef contract, issue #144 T12).

All cases exercise the real SttNatsAdapter code.  api.transcribe is patched at
the source module so actual coverage runs through the adapter and runner.

V2 changes vs V1:
- Payloads carry ``blob_ref`` (dict) instead of ``audio_b64`` (str).
- Runner fetches audio bytes from BlobStore (mocked via blobs.get_blobstore).
- Runner owns scoped-path creation; adapter receives scoped_path via 3-tuple.
- ``audio_decode_failed`` error code replaced by ``audio_fetch_failed``.
- ``payload_too_large`` / ``audio_decode_failed`` tests removed (no b64 path).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Lazy import shim — lets --collect-only succeed even before the module exists
# ---------------------------------------------------------------------------

try:
    from voicecli.api import ParamValidationError
    from voicecli.adapters.nats.config import DEFAULT_MODEL
    from voicecli.adapters.nats.queue_groups import STT_WORKERS
    from voicecli.adapters.nats.transcribe_adapter import (
        HEARTBEAT_SUBJECT,
        SUBJECT,
        SttNatsAdapter,
        _duration_from_segments,
        _ext_from_mime,
    )
    from voicecli.runtime.transcribe import Segment, TranscriptionResult

    _IMPORT_ERROR: ImportError | None = None
except ImportError as _e:
    _IMPORT_ERROR = _e
    ParamValidationError = ValueError  # type: ignore[assignment,misc]
    SttNatsAdapter = None  # type: ignore[assignment,misc]
    _duration_from_segments = None  # type: ignore[assignment]
    _ext_from_mime = None  # type: ignore[assignment]
    TranscriptionResult = None  # type: ignore[assignment]
    DEFAULT_MODEL = "large-v3-turbo"  # type: ignore[assignment]
    SUBJECT = "lyra.voice.stt.request"  # type: ignore[assignment]
    HEARTBEAT_SUBJECT = "lyra.voice.stt.heartbeat"  # type: ignore[assignment]
    STT_WORKERS = "stt-workers"  # type: ignore[assignment]


def _require_imports() -> None:
    if _IMPORT_ERROR is not None:
        pytest.fail(
            f"voicecli.adapters.nats.transcribe_adapter not yet implemented (RED): {_IMPORT_ERROR}"
        )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

from _fakes import FakeMsg as MockMsg  # noqa: E402
from _fakes import FakeNatsConn  # noqa: E402

from roxabi_blobs import BlobRef  # noqa: E402

_NOW = datetime.now(timezone.utc)


def _make_blob_ref(
    *,
    store_key: str = "sha256:deadbeef",
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


class _FakeBlobStore:
    """Async BlobStore that returns minimal WAV-ish bytes for any store_key."""

    def __init__(self, data: bytes = b"\x00" * 32) -> None:
        self._data = data

    async def get(self, store_key: str) -> bytes:
        return self._data


class _RaisingBlobStore:
    async def get(self, store_key: str) -> bytes:
        raise RuntimeError("blobstore_unreachable")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_adapter(adapter: "SttNatsAdapter", msg: MockMsg) -> None:
    adapter._nc = FakeNatsConn(msg)


def _valid_payload(
    *,
    request_id: str = "req-001",
    blob_ref: BlobRef | None = None,
    contract_version: str = "1",
    trace_id: str | None = "test-trace-001",
) -> dict:
    ref = blob_ref if blob_ref is not None else _make_blob_ref()
    payload: dict = {
        "contract_version": contract_version,
        "request_id": request_id,
        "blob_ref": {
            "store_key": ref.store_key,
            "mime": ref.mime,
            "size": ref.size,
            "source": ref.source,
            "content_hash": ref.content_hash,
            "created_at": ref.created_at.isoformat(),
        },
    }
    if trace_id is not None:
        payload["trace_id"] = trace_id
    return payload


def _fake_result(
    text: str = "hello world",
    language: str = "en",
    end: float = 2.5,
) -> "TranscriptionResult":
    return TranscriptionResult(
        text=text,
        language=language,
        segments=[Segment(start=0.0, end=end, text=text)],
    )


def _make_adapter(**kwargs) -> "SttNatsAdapter":
    defaults = {
        "default_model": DEFAULT_MODEL,
        "max_concurrent": 2,
    }
    defaults.update(kwargs)
    adapter = SttNatsAdapter(**defaults)
    adapter._model_warm = True
    return adapter


def _patch_transcribe(
    mock_result: "TranscriptionResult | None" = None,
    *,
    side_effect=None,
):
    if mock_result is not None and side_effect is not None:
        raise TypeError("_patch_transcribe: pass either mock_result or side_effect, not both")
    if side_effect is not None:
        return patch("voicecli.api.transcribe", side_effect=side_effect)
    return patch("voicecli.api.transcribe", return_value=mock_result)


def _patch_blobstore(store=None):
    """Patch blobs.get_blobstore to return *store* (defaults to _FakeBlobStore)."""
    s = store if store is not None else _FakeBlobStore()
    return patch("voicecli.adapters.nats.blobs.get_blobstore", return_value=s)


def _patch_temp_root(tmp_path: Path):
    """Redirect TEMP_ROOT so runner writes into tmp_path."""
    return patch("voicecli.adapters.nats.transcribe_adapter.TEMP_ROOT", tmp_path)


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestSttNatsAdapter:
    # ------------------------------------------------------------------
    # Case 1: happy path
    # ------------------------------------------------------------------
    def test_happy_path(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-001")

        with _patch_blobstore():
            with _patch_transcribe(_fake_result()) as mock_transcribe:
                with _patch_temp_root(tmp_path):
                    asyncio.run(adapter.handle(msg, payload))

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is True
        assert reply["contract_version"] == "1"
        assert reply["request_id"] == "req-001"
        assert reply["trace_id"] == "test-trace-001"
        from datetime import datetime as _dt

        _iat = _dt.fromisoformat(reply["issued_at"])
        assert _iat.tzinfo is not None
        assert reply["text"] == "hello world"
        assert reply["language"] == "en"
        assert reply["duration_seconds"] == pytest.approx(2.5)
        mock_transcribe.assert_called_once()

    def test_reply_uses_unknown_trace_id_when_absent(self, tmp_path: Path) -> None:
        _require_imports()
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-notrace", trace_id=None)

        with _patch_blobstore():
            with _patch_transcribe(_fake_result()):
                with _patch_temp_root(tmp_path):
                    asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is True
        assert reply["trace_id"] == "unknown"

    # ------------------------------------------------------------------
    # Case 2: missing request_id
    # ------------------------------------------------------------------
    def test_malformed_request_id_missing(self) -> None:
        _require_imports()
        # Arrange — blob_ref present but request_id absent
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = {
            "blob_ref": {
                "store_key": "sha256:x",
                "mime": "audio/wav",
                "size": 32,
                "source": "test",
                "content_hash": "x",
                "created_at": _NOW.isoformat(),
            }
        }

        asyncio.run(adapter.handle(msg, payload))

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"
        assert reply["request_id"] == ""
        assert "trace_id" in reply
        assert "issued_at" in reply

    # ------------------------------------------------------------------
    # Case 3: request_id with invalid characters
    # ------------------------------------------------------------------
    def test_malformed_request_id_bad_chars(self) -> None:
        _require_imports()
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        bad_id = "../etc/passwd"
        payload = _valid_payload(request_id=bad_id)

        asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"
        assert reply["request_id"] == bad_id[:64]

    # ------------------------------------------------------------------
    # Case 4: blob_ref key absent
    # ------------------------------------------------------------------
    def test_malformed_blob_ref_missing(self) -> None:
        _require_imports()
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = {"contract_version": "1", "request_id": "req-noblob"}

        asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"

    # ------------------------------------------------------------------
    # Case 5: blob_ref is not a dict
    # ------------------------------------------------------------------
    def test_malformed_blob_ref_not_dict(self) -> None:
        _require_imports()
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = {"contract_version": "1", "request_id": "req-badtype", "blob_ref": "notadict"}

        asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"

    # ------------------------------------------------------------------
    # Case 5b: wrong-type override fields yield malformed_request
    # ------------------------------------------------------------------
    def test_malformed_language_detection_threshold_type(self) -> None:
        _require_imports()
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload()
        payload["language_detection_threshold"] = "high"

        asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"

    def test_malformed_language_detection_segments_bool(self) -> None:
        _require_imports()
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload()
        payload["language_detection_segments"] = True

        asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"

    # ------------------------------------------------------------------
    # Case 6 (V2): BlobStore.get raises → audio_fetch_failed
    # ------------------------------------------------------------------
    def test_audio_fetch_failed_when_blobstore_raises(self, tmp_path: Path) -> None:
        """BlobStore.get() raises → adapter replies audio_fetch_failed.

        Negative-test: removing the try/except in run_transcription around
        get_blobstore().get() would propagate the exception rather than
        returning the error tuple — this test would then fail on missing reply.
        """
        _require_imports()
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-fetchfail")

        with _patch_blobstore(_RaisingBlobStore()):
            with _patch_transcribe(_fake_result()) as mock_transcribe:
                with _patch_temp_root(tmp_path):
                    asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "audio_fetch_failed"
        mock_transcribe.assert_not_called()

    # ------------------------------------------------------------------
    # Case 7: api.transcribe raises — reply transcription_failed, no re-raise
    # ------------------------------------------------------------------
    def test_transcription_failed(self, tmp_path: Path) -> None:
        _require_imports()
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-boom")

        with _patch_blobstore():
            with patch("voicecli.api.transcribe", side_effect=RuntimeError("model OOM")):
                with _patch_temp_root(tmp_path):
                    asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "transcription_failed"

    # ------------------------------------------------------------------
    # Case 8: contract_version "999" — defensive handling, still ok=true
    # ------------------------------------------------------------------
    def test_contract_version_defensive(self, tmp_path: Path) -> None:
        _require_imports()
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-999", contract_version="999")

        with _patch_blobstore():
            with _patch_transcribe(_fake_result()):
                with _patch_temp_root(tmp_path):
                    asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is True
        assert reply["contract_version"] == "1"

    # ------------------------------------------------------------------
    # Case 9: request_id echoed in reply
    # ------------------------------------------------------------------
    def test_request_id_echoed(self, tmp_path: Path) -> None:
        _require_imports()
        rid = "my-unique-req-42"
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id=rid)

        with _patch_blobstore():
            with _patch_transcribe(_fake_result()):
                with _patch_temp_root(tmp_path):
                    asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["request_id"] == rid

    # ------------------------------------------------------------------
    # Case 10: language forced in request, echoed in reply, passed to api
    # ------------------------------------------------------------------
    def test_language_forced_echoed(self, tmp_path: Path) -> None:
        _require_imports()
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-fr")
        payload["language"] = "fr"
        mock_result = _fake_result(language="fr")

        with _patch_blobstore():
            with _patch_transcribe(mock_result) as mock_transcribe:
                with _patch_temp_root(tmp_path):
                    asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is True
        assert reply["language"] == "fr"
        call_kwargs = mock_transcribe.call_args.kwargs
        assert call_kwargs.get("language") == "fr"

    # ------------------------------------------------------------------
    # Case 11: all override fields passed through to api.transcribe
    # ------------------------------------------------------------------
    def test_overrides_applied(self, tmp_path: Path) -> None:
        _require_imports()
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-overrides")
        payload["language"] = "de"
        payload["language_detection_threshold"] = 0.7
        payload["language_detection_segments"] = 3
        payload["language_fallback"] = "en"
        payload["initial_prompt"] = "Hallo. Hier ist deutscher Text mit Zeichensetzung."
        payload["task"] = "transcribe"

        with _patch_blobstore():
            with _patch_transcribe(_fake_result(language="de")) as mock_transcribe:
                with _patch_temp_root(tmp_path):
                    asyncio.run(adapter.handle(msg, payload))

        call_kwargs = mock_transcribe.call_args.kwargs
        assert call_kwargs.get("language") == "de"
        assert call_kwargs.get("language_detection_threshold") == pytest.approx(0.7)
        assert call_kwargs.get("language_detection_segments") == 3
        assert call_kwargs.get("language_fallback") == "en"
        assert call_kwargs.get("initial_prompt") == payload["initial_prompt"]
        assert call_kwargs.get("task") == "transcribe"
        assert call_kwargs.get("_skip_daemon") is True

    # ------------------------------------------------------------------
    # Case 11b: invalid task value rejected as malformed_request
    # ------------------------------------------------------------------
    def test_invalid_task_rejected(self, tmp_path: Path) -> None:
        _require_imports()
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-bad-task")
        payload["task"] = "summarize"

        with _patch_blobstore():
            with _patch_transcribe(_fake_result()):
                with _patch_temp_root(tmp_path):
                    asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"

    # ------------------------------------------------------------------
    # Case 12: override fields do not leak between sequential requests
    # ------------------------------------------------------------------
    def test_overrides_no_leakage(self, tmp_path: Path) -> None:
        _require_imports()
        adapter = _make_adapter(max_concurrent=2)
        msg1, msg2 = MockMsg(), MockMsg()
        _setup_adapter(adapter, msg1)
        _setup_adapter(adapter, msg2)

        payload1 = _valid_payload(request_id="req-with-overrides")
        payload1["language"] = "de"
        payload1["language_detection_threshold"] = 0.8
        payload1["language_detection_segments"] = 5
        payload1["language_fallback"] = "en"

        payload2 = _valid_payload(request_id="req-no-overrides")

        with _patch_blobstore():
            with patch("voicecli.api.transcribe", return_value=_fake_result()) as mock_transcribe:
                with _patch_temp_root(tmp_path):
                    asyncio.run(adapter.handle(msg1, payload1))
                    asyncio.run(adapter.handle(msg2, payload2))

        kwargs1 = mock_transcribe.call_args_list[0].kwargs
        assert kwargs1.get("language") == "de"
        assert kwargs1.get("language_detection_threshold") == pytest.approx(0.8)
        assert kwargs1.get("language_detection_segments") == 5
        assert kwargs1.get("language_fallback") == "en"

        kwargs2 = mock_transcribe.call_args_list[1].kwargs
        assert "language" not in kwargs2
        assert "language_detection_threshold" not in kwargs2
        assert "language_detection_segments" not in kwargs2
        assert "language_fallback" not in kwargs2

    # ------------------------------------------------------------------
    # Case 13: first heartbeat — model_loaded is None before any request
    # ------------------------------------------------------------------
    def test_first_heartbeat_null_model(self) -> None:
        _require_imports()
        adapter = _make_adapter()
        adapter._nc = FakeNatsConn()

        hb = adapter.heartbeat_payload()

        assert hb["model_loaded"] is None
        assert hb["active_requests"] == 0
        assert hb["service"] == STT_WORKERS
        assert hb["subject"] == SUBJECT
        assert hb["queue_group"] == STT_WORKERS

    # ------------------------------------------------------------------
    # Case 14: heartbeat payload includes all required fields
    # ------------------------------------------------------------------
    def test_heartbeat_fields(self) -> None:
        _require_imports()
        adapter = _make_adapter()
        adapter._nc = FakeNatsConn()

        hb = adapter.heartbeat_payload()

        required_keys = {
            "contract_version",
            "worker_id",
            "service",
            "host",
            "subject",
            "queue_group",
            "ts",
            "model_loaded",
            "active_requests",
        }
        missing = required_keys - set(hb.keys())
        assert not missing, f"Missing heartbeat keys: {missing}"

    # ------------------------------------------------------------------
    # Case 15: max_concurrent=1, two sequential requests do not overlap
    # ------------------------------------------------------------------
    def test_max_concurrent_limit(self, tmp_path: Path) -> None:
        _require_imports()
        enter_times: list[float] = []
        exit_times: list[float] = []

        def _slow_transcribe(*args, **kwargs):
            enter_times.append(time.monotonic())
            time.sleep(0.05)
            exit_times.append(time.monotonic())
            return _fake_result()

        adapter = _make_adapter(max_concurrent=1)

        async def _run() -> None:
            with _patch_blobstore():
                with patch("voicecli.api.transcribe", side_effect=_slow_transcribe):
                    with _patch_temp_root(tmp_path):
                        msg1, msg2 = MockMsg(), MockMsg()
                        _setup_adapter(adapter, msg1)
                        _setup_adapter(adapter, msg2)
                        p1 = _valid_payload(request_id="req-c1")
                        p2 = _valid_payload(request_id="req-c2")
                        await asyncio.gather(
                            adapter.handle(msg1, p1),
                            adapter.handle(msg2, p2),
                        )

        asyncio.run(_run())

        assert len(enter_times) == 2
        assert len(exit_times) == 2
        assert enter_times[1] >= exit_times[0], (
            f"Requests overlapped: enter[1]={enter_times[1]:.4f} < exit[0]={exit_times[0]:.4f}"
        )

    # ------------------------------------------------------------------
    # Case 16: reject_when_full=True → capacity_exceeded
    # ------------------------------------------------------------------
    def test_reject_when_full(self) -> None:
        _require_imports()
        adapter = _make_adapter(max_concurrent=1, reject_when_full=True)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-cap")

        async def _run() -> None:
            await adapter._sem.acquire()  # type: ignore[attr-defined]
            try:
                await adapter.handle(msg, payload)
            finally:
                adapter._sem.release()  # type: ignore[attr-defined]

        asyncio.run(_run())

        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "capacity_exceeded"

    # ------------------------------------------------------------------
    # Case 17: inbound audio mode 0o600 (runner owns file creation)
    # ------------------------------------------------------------------
    def test_inbound_audio_written_with_mode_0o600(self, tmp_path: Path) -> None:
        """Runner must chmod the fetched audio file to 0o600.

        The original _write_bytes_leak trick is preserved: we widen the mode to
        0o644 just after write_bytes, then assert the runner's chmod(0o600)
        closed the gap.
        """
        _require_imports()
        import os
        import stat as _stat

        request_id = "req-mode-0600"
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id=request_id)
        observed: dict[str, int] = {}

        def _sniff_transcribe(audio_path, **kwargs):
            observed["mode"] = _stat.S_IMODE(os.stat(audio_path).st_mode)
            return _fake_result()

        original_write_bytes = Path.write_bytes

        def _write_bytes_leak(self: Path, data: bytes) -> int:
            result = original_write_bytes(self, data)
            if self.name.startswith(request_id):
                self.chmod(0o644)
            return result

        with _patch_blobstore():
            with (
                patch("voicecli.api.transcribe", side_effect=_sniff_transcribe),
                patch.object(Path, "write_bytes", _write_bytes_leak),
                _patch_temp_root(tmp_path),
            ):
                asyncio.run(adapter.handle(msg, payload))

        assert msg.last_reply()["ok"] is True
        assert "mode" in observed, "api.transcribe was never called — sniff never ran"
        assert observed["mode"] == 0o600, (
            f"inbound audio mode at transcribe was {oct(observed['mode'])}, expected 0o600"
        )

    def test_temp_file_cleaned_up_on_success(self, tmp_path: Path) -> None:
        _require_imports()
        request_id = "req-clean-ok"
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id=request_id)

        with _patch_blobstore():
            with _patch_transcribe(_fake_result()):
                with _patch_temp_root(tmp_path):
                    asyncio.run(adapter.handle(msg, payload))

        # Assert — no leftover temp files under tmp_path
        leftover = list(tmp_path.iterdir())
        assert leftover == [], f"Temp files not cleaned up: {leftover}"

    # ------------------------------------------------------------------
    # Case 18: temp file cleaned up even when api.transcribe raises
    # ------------------------------------------------------------------
    def test_temp_file_cleaned_up_on_failure(self, tmp_path: Path) -> None:
        _require_imports()
        request_id = "req-clean-fail"
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id=request_id)

        with _patch_blobstore():
            with patch("voicecli.api.transcribe", side_effect=RuntimeError("kaboom")):
                with _patch_temp_root(tmp_path):
                    asyncio.run(adapter.handle(msg, payload))

        leftover = list(tmp_path.iterdir())
        assert leftover == [], f"Temp files not cleaned up on failure: {leftover}"

    # ------------------------------------------------------------------
    # Case 22 (F12): heartbeat continues firing while inference is in-flight
    # ------------------------------------------------------------------
    def test_heartbeat_continues_during_inference(self, tmp_path: Path) -> None:
        """Heartbeat loop keeps firing while transcription is in-flight.

        Blocks on a threading.Event tripped after target_heartbeats fires.
        """
        _require_imports()

        gate = threading.Event()
        heartbeat_count = 0
        target_heartbeats = 3

        async def _run() -> None:
            nonlocal heartbeat_count
            adapter = _make_adapter(max_concurrent=1, heartbeat_interval=0.05)
            msg = MockMsg()
            _setup_adapter(adapter, msg)
            payload = _valid_payload(request_id="req-hb")

            original_publish = adapter._nc.publish  # type: ignore[union-attr]

            async def _counting_publish(subject: str, data: bytes) -> None:
                nonlocal heartbeat_count
                if "heartbeat" in subject:
                    heartbeat_count += 1
                    if heartbeat_count >= target_heartbeats:
                        gate.set()
                await original_publish(subject, data)

            adapter._nc.publish = _counting_publish  # type: ignore[union-attr, method-assign]

            def _gated_transcribe(*args, **kwargs):
                gate.wait(timeout=5.0)
                return TranscriptionResult(
                    text="ok",
                    language="en",
                    segments=[Segment(start=0.0, end=1.5, text="ok")],
                )

            with _patch_blobstore():
                with patch("voicecli.api.transcribe", side_effect=_gated_transcribe):
                    with _patch_temp_root(tmp_path):
                        handle_task = asyncio.create_task(adapter.handle(msg, payload))
                        hb_task = asyncio.create_task(adapter._heartbeat_loop())  # type: ignore[attr-defined]
                        try:
                            await asyncio.wait_for(handle_task, timeout=5.0)
                        finally:
                            hb_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await hb_task

        asyncio.run(_run())

        assert heartbeat_count >= target_heartbeats, (
            f"heartbeat loop did not fire enough: got {heartbeat_count}, "
            f"expected >= {target_heartbeats}"
        )

    # ------------------------------------------------------------------
    # Validation error code
    # ------------------------------------------------------------------
    def test_value_error_from_transcribe_yields_param_validation_failed(
        self, tmp_path: Path
    ) -> None:
        """ValueError from api.transcribe → param_validation_failed."""
        _require_imports()
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-stt-valerr")

        with _patch_blobstore():
            with _patch_transcribe(side_effect=ParamValidationError("invalid language: xx")):
                with _patch_temp_root(tmp_path):
                    asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "param_validation_failed"

    @pytest.mark.parametrize(
        "rid_len, expected_ok",
        [(127, True), (128, True), (129, False)],
    )
    def test_request_id_length_boundary(
        self, tmp_path: Path, rid_len: int, expected_ok: bool
    ) -> None:
        _require_imports()
        rid = "a" * rid_len
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id=rid)

        with _patch_blobstore():
            with _patch_transcribe(_fake_result()) as mock_transcribe:
                with _patch_temp_root(tmp_path):
                    asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        if expected_ok:
            assert reply.get("error") is None
            assert reply["ok"] is True
        else:
            assert reply["ok"] is False
            assert reply["error"] == "malformed_request"
            mock_transcribe.assert_not_called()
