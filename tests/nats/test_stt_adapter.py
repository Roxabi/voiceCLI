"""Tests for SttNatsAdapter (issue #44 T4).

Mirrors the structure of test_tts_adapter.py.  All 16 cases exercise the
real SttNatsAdapter code — api.transcribe is patched at the import site
(voicecli.nats.stt_adapter.api) so actual coverage runs through the adapter.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Lazy import shim — lets --collect-only succeed even before the module exists
# ---------------------------------------------------------------------------

try:
    from voicecli.nats.config import DEFAULT_MODEL
    from voicecli.nats.queue_groups import STT_WORKERS
    from voicecli.nats.stt_adapter import (
        HEARTBEAT_SUBJECT,
        SUBJECT,
        SttNatsAdapter,
        _duration_from_segments,
        _ext_from_mime,
    )
    from voicecli.transcribe import TranscriptionResult

    _IMPORT_ERROR: ImportError | None = None
except ImportError as _e:
    _IMPORT_ERROR = _e
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
        pytest.fail(f"voicecli.nats.stt_adapter not yet implemented (RED): {_IMPORT_ERROR}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Canonical message stand-in lives in tests/nats/_fakes.py — aliased here so
# every existing MockMsg() call site keeps working unchanged.
from _fakes import FakeMsg as MockMsg  # noqa: E402
from _fakes import FakeNatsConn  # noqa: E402


def _setup_adapter(adapter: "SttNatsAdapter", msg: MockMsg) -> None:
    """Set up adapter with mock NATS connection for testing.

    The SDK's reply() method requires _nc to be set. This helper
    sets up a FakeNatsConn that forwards publishes to msg.respond().
    """
    adapter._nc = FakeNatsConn(msg)  # noqa: E402


def _valid_audio_b64() -> str:
    """Return a valid base64-encoded minimal WAV-ish byte blob."""
    return base64.b64encode(b"\x00" * 16).decode()


def _valid_payload(
    *,
    request_id: str = "req-001",
    audio_b64: str | None = None,
    contract_version: str = "1",
    mime_type: str = "audio/wav",
    trace_id: str | None = "test-trace-001",
) -> dict:
    payload: dict = {
        "contract_version": contract_version,
        "request_id": request_id,
        "audio_b64": audio_b64 if audio_b64 is not None else _valid_audio_b64(),
        "mime_type": mime_type,
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
        segments=[{"start": 0.0, "end": end, "text": text}],
    )


def _make_adapter(**kwargs) -> "SttNatsAdapter":
    defaults = {
        "default_model": DEFAULT_MODEL,
        "max_concurrent": 2,
    }
    defaults.update(kwargs)
    adapter = SttNatsAdapter(**defaults)
    # Short-circuit the model warm-up step so tests don't pull faster_whisper/torch
    # into sys.modules. Tests that need to exercise warm-up failure patch
    # `voicecli.transcribe._load_model` to raise.
    adapter._model_warm = True
    return adapter


def _patch_transcribe(mock_result: "TranscriptionResult"):
    """Context manager: patch api.transcribe at the adapter import site."""
    # The deferred `from voicecli import api` inside _run_transcription binds
    # `api` on the stt_adapter module namespace.  We force that binding here
    # so patch.object can reach it.
    import voicecli.nats.stt_adapter as _mod
    import voicecli.api as _api  # noqa: F401 — materialises the attribute

    _mod.api = _api  # type: ignore[attr-defined]
    return patch("voicecli.nats.stt_adapter.api.transcribe", return_value=mock_result)


def _patch_scoped_path(tmp_path: Path):
    """Redirect scoped_path to tmp_path so tests don't touch /tmp/voicecli-nats."""

    def _impl(request_id: str, ext: str) -> Path:
        p = tmp_path / f"{request_id}.{ext}"
        return p

    return patch("voicecli.nats.stt_adapter.scoped_path", side_effect=_impl)


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

        with _patch_transcribe(_fake_result()) as mock_transcribe:
            with _patch_scoped_path(tmp_path):
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

        with _patch_transcribe(_fake_result()) as _:
            with _patch_scoped_path(tmp_path):
                asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is True
        assert reply["trace_id"] == "unknown"

    # ------------------------------------------------------------------
    # Case 2: missing request_id
    # ------------------------------------------------------------------
    def test_malformed_request_id_missing(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = {"audio_b64": _valid_audio_b64()}

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
    def test_malformed_request_id_bad_chars(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        bad_id = "../etc/passwd"
        payload = _valid_payload(request_id=bad_id)

        asyncio.run(adapter.handle(msg, payload))

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"
        # request_id echoed as first 64 chars of the bad value
        assert reply["request_id"] == bad_id[:64]

    # ------------------------------------------------------------------
    # Case 4: audio_b64 key absent
    # ------------------------------------------------------------------
    def test_malformed_audio_b64_missing(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = {"contract_version": "1", "request_id": "req-noaudio"}

        asyncio.run(adapter.handle(msg, payload))

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"

    # ------------------------------------------------------------------
    # Case 5: audio_b64 is not a string
    # ------------------------------------------------------------------
    def test_malformed_audio_b64_not_str(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = {"contract_version": "1", "request_id": "req-badtype", "audio_b64": 123}

        asyncio.run(adapter.handle(msg, payload))

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"

    # ------------------------------------------------------------------
    # Case 6: base64 decode failure — transcribe must NOT be called
    # ------------------------------------------------------------------
    def test_audio_decode_failed(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(audio_b64="!!!not-base64!!!")

        with _patch_transcribe(_fake_result()) as mock_transcribe:
            with _patch_scoped_path(tmp_path):
                asyncio.run(adapter.handle(msg, payload))

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "audio_decode_failed"
        mock_transcribe.assert_not_called()

    # ------------------------------------------------------------------
    # Case 7: api.transcribe raises — reply transcription_failed, no re-raise
    # ------------------------------------------------------------------
    def test_transcription_failed(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-boom")

        import voicecli.nats.stt_adapter as _mod
        import voicecli.api as _api  # noqa: F401

        _mod.api = _api  # type: ignore[attr-defined]

        with patch(
            "voicecli.nats.stt_adapter.api.transcribe",
            side_effect=RuntimeError("model OOM"),
        ):
            with _patch_scoped_path(tmp_path):
                # Must not raise — adapter swallows the exception
                asyncio.run(adapter.handle(msg, payload))

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "transcription_failed"

    # ------------------------------------------------------------------
    # Case 8: contract_version "999" — defensive handling, still ok=true
    # ------------------------------------------------------------------
    def test_contract_version_defensive(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-999", contract_version="999")

        with _patch_transcribe(_fake_result()) as _:
            with _patch_scoped_path(tmp_path):
                asyncio.run(adapter.handle(msg, payload))

        # Assert — succeeds; reply stamps contract_version "1"
        reply = msg.last_reply()
        assert reply["ok"] is True
        assert reply["contract_version"] == "1"

    # ------------------------------------------------------------------
    # Case 9: request_id echoed in reply
    # ------------------------------------------------------------------
    def test_request_id_echoed(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        rid = "my-unique-req-42"
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id=rid)

        with _patch_transcribe(_fake_result()) as _:
            with _patch_scoped_path(tmp_path):
                asyncio.run(adapter.handle(msg, payload))

        # Assert
        reply = msg.last_reply()
        assert reply["request_id"] == rid

    # ------------------------------------------------------------------
    # Case 10: language forced in request, echoed in reply, passed to api
    # ------------------------------------------------------------------
    def test_language_forced_echoed(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-fr")
        payload["language"] = "fr"

        mock_result = _fake_result(language="fr")

        with _patch_transcribe(mock_result) as mock_transcribe:
            with _patch_scoped_path(tmp_path):
                asyncio.run(adapter.handle(msg, payload))

        # Assert reply
        reply = msg.last_reply()
        assert reply["ok"] is True
        assert reply["language"] == "fr"

        # Assert api.transcribe called with language="fr"
        call_kwargs = mock_transcribe.call_args.kwargs
        assert call_kwargs.get("language") == "fr"

    # ------------------------------------------------------------------
    # Case 11: all override fields passed through to api.transcribe
    # ------------------------------------------------------------------
    def test_overrides_applied(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
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

        with _patch_transcribe(_fake_result(language="de")) as mock_transcribe:
            with _patch_scoped_path(tmp_path):
                asyncio.run(adapter.handle(msg, payload))

        # Assert
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
        payload["task"] = "summarize"  # not in {"transcribe", "translate"}

        with _patch_transcribe(_fake_result()):
            with _patch_scoped_path(tmp_path):
                asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"

    # ------------------------------------------------------------------
    # Case 12: override fields do not leak between sequential requests
    # ------------------------------------------------------------------
    def test_overrides_no_leakage(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange — same adapter instance, two sequential calls
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
        # payload2 has NO override fields

        import voicecli.nats.stt_adapter as _mod
        import voicecli.api as _api  # noqa: F401

        _mod.api = _api  # type: ignore[attr-defined]

        with patch(
            "voicecli.nats.stt_adapter.api.transcribe",
            return_value=_fake_result(),
        ) as mock_transcribe:
            with _patch_scoped_path(tmp_path):
                asyncio.run(adapter.handle(msg1, payload1))
                asyncio.run(adapter.handle(msg2, payload2))

        # Assert call 1 had overrides
        kwargs1 = mock_transcribe.call_args_list[0].kwargs
        assert kwargs1.get("language") == "de"
        assert kwargs1.get("language_detection_threshold") == pytest.approx(0.8)
        assert kwargs1.get("language_detection_segments") == 5
        assert kwargs1.get("language_fallback") == "en"

        # Assert call 2 did NOT leak any override keys
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
    # VRAM fields (vram_used_mb, vram_total_mb) dropped in SDK migration.
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
        # Arrange — track order of entry/exit to confirm no overlap.
        # api.transcribe runs via run_in_executor (sync thread), so the mock
        # must be a plain synchronous function, not a coroutine.
        enter_times: list[float] = []
        exit_times: list[float] = []

        def _slow_transcribe(*args, **kwargs):
            enter_times.append(time.monotonic())
            time.sleep(0.05)  # blocking sleep — correct for a thread-pool mock
            exit_times.append(time.monotonic())
            return _fake_result()

        adapter = _make_adapter(max_concurrent=1)

        import voicecli.nats.stt_adapter as _mod
        import voicecli.api as _api  # noqa: F401

        _mod.api = _api  # type: ignore[attr-defined]

        async def _run() -> None:
            with patch(
                "voicecli.nats.stt_adapter.api.transcribe",
                side_effect=_slow_transcribe,
            ):
                with patch(
                    "voicecli.nats.stt_adapter.scoped_path",
                    side_effect=lambda rid, ext: tmp_path / f"{rid}.{ext}",
                ):
                    msg1, msg2 = MockMsg(), MockMsg()
                    _setup_adapter(adapter, msg1)
                    _setup_adapter(adapter, msg2)
                    p1 = _valid_payload(request_id="req-c1")
                    p2 = _valid_payload(request_id="req-c2")
                    # Fire both concurrently — semaphore serialises them
                    await asyncio.gather(
                        adapter.handle(msg1, p1),
                        adapter.handle(msg2, p2),
                    )

        asyncio.run(_run())

        # With max_concurrent=1, the second request must not enter before
        # the first has exited — i.e. enter_times[1] >= exit_times[0].
        assert len(enter_times) == 2
        assert len(exit_times) == 2
        assert enter_times[1] >= exit_times[0], (
            f"Requests overlapped: enter[1]={enter_times[1]:.4f} < exit[0]={exit_times[0]:.4f}"
        )

    # ------------------------------------------------------------------
    # Case 16: reject_when_full=True → capacity_exceeded
    # ------------------------------------------------------------------
    def test_reject_when_full(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        adapter = _make_adapter(max_concurrent=1, reject_when_full=True)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-cap")

        async def _run() -> None:
            # Hold the semaphore to simulate an in-flight request
            await adapter._sem.acquire()  # type: ignore[attr-defined]
            try:
                await adapter.handle(msg, payload)
            finally:
                adapter._sem.release()  # type: ignore[attr-defined]

        asyncio.run(_run())

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "capacity_exceeded"

    # ------------------------------------------------------------------
    # Case 17: temp file cleaned up on successful transcription
    # ------------------------------------------------------------------
    def test_inbound_audio_written_with_mode_0o600(self, tmp_path: Path) -> None:
        """Issue #60: inbound audio must be 0o600 on disk, not world-readable 0o644.

        Intercepts `Path.write_bytes` to widen mode to 0o644 after the adapter's
        own write, so the assertion proves the adapter's explicit `chmod(0o600)`
        closes the gap — not that umask happened to already be 0o077.
        """
        _require_imports()
        import os
        import stat as _stat

        request_id = "req-mode-0600"
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id=request_id)
        temp_file = tmp_path / f"{request_id}.wav"

        observed: dict[str, int] = {}

        def _sniff_transcribe(audio_path, **kwargs):
            observed["mode"] = _stat.S_IMODE(os.stat(audio_path).st_mode)
            return _fake_result()

        import voicecli.api as _api  # noqa: F401
        import voicecli.nats.stt_adapter as _mod

        _mod.api = _api  # type: ignore[attr-defined]

        original_write_bytes = Path.write_bytes

        def _write_bytes_leak(self: Path, data: bytes) -> int:
            """Simulate the pre-fix vulnerable state: file lands 0o644 on disk."""
            result = original_write_bytes(self, data)
            if self == temp_file:
                self.chmod(0o644)
            return result

        with (
            patch("voicecli.nats.stt_adapter.api.transcribe", side_effect=_sniff_transcribe),
            patch.object(Path, "write_bytes", _write_bytes_leak),
            _patch_scoped_path(tmp_path),
        ):
            asyncio.run(adapter.handle(msg, payload))

        assert msg.last_reply()["ok"] is True
        assert not temp_file.exists()  # cleanup still works
        assert "mode" in observed, "api.transcribe was never called — sniff never ran"
        assert observed["mode"] == 0o600, (
            f"inbound audio mode at transcribe was "
            f"{oct(observed['mode'])}, expected 0o600 (issue #60)"
        )

    def test_temp_file_cleaned_up_on_success(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        request_id = "req-clean-ok"
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id=request_id)
        temp_file = tmp_path / f"{request_id}.wav"

        with _patch_transcribe(_fake_result()) as _:
            with _patch_scoped_path(tmp_path):
                asyncio.run(adapter.handle(msg, payload))

        # Assert — temp file removed after successful reply
        assert not temp_file.exists()

    # ------------------------------------------------------------------
    # Case 18: temp file cleaned up even when api.transcribe raises
    # ------------------------------------------------------------------
    def test_temp_file_cleaned_up_on_failure(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        request_id = "req-clean-fail"
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id=request_id)
        temp_file = tmp_path / f"{request_id}.wav"

        import voicecli.nats.stt_adapter as _mod
        import voicecli.api as _api  # noqa: F401

        _mod.api = _api  # type: ignore[attr-defined]

        with patch(
            "voicecli.nats.stt_adapter.api.transcribe",
            side_effect=RuntimeError("kaboom"),
        ):
            with _patch_scoped_path(tmp_path):
                asyncio.run(adapter.handle(msg, payload))

        # Assert — temp file removed even when transcription fails
        assert not temp_file.exists()

    # ------------------------------------------------------------------
    # Case 22 (F12): heartbeat continues firing while inference is in-flight
    # ------------------------------------------------------------------
    def test_heartbeat_continues_during_inference(self, tmp_path: Path) -> None:
        """The heartbeat loop keeps firing while transcription is in-flight.

        Deterministic gate: transcription runs inside ``run_in_executor`` so
        it blocks on a ``threading.Event`` that is set once the heartbeat
        publisher has been called at least ``target_heartbeats`` times. If
        the loop stops firing, the gate never sets and ``wait_for`` below
        times out with a clean failure. No wall-clock bounds.

        Floor ``target_heartbeats = 3`` catches "fires once at entry"; no
        ceiling — a fast runner is not a bug.
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

            # Wrap the FakeNatsConn.publish set up by _setup_adapter to count
            # heartbeat publishes and trip the gate once the floor is hit.
            original_publish = adapter._nc.publish  # type: ignore[union-attr]

            async def _counting_publish(subject: str, data: bytes) -> None:
                nonlocal heartbeat_count
                if "heartbeat" in subject:
                    heartbeat_count += 1
                    if heartbeat_count >= target_heartbeats:
                        gate.set()
                await original_publish(subject, data)

            adapter._nc.publish = _counting_publish  # type: ignore[union-attr, method-assign]

            # Blocks until the heartbeat loop proves liveness via the gate
            def _gated_transcribe(*args, **kwargs):
                gate.wait(timeout=5.0)
                return TranscriptionResult(
                    text="ok",
                    language="en",
                    segments=[{"start": 0.0, "end": 1.5, "text": "ok"}],
                )

            import voicecli.nats.stt_adapter as _mod
            import voicecli.api as _api  # noqa: F401

            _mod.api = _api  # type: ignore[attr-defined]

            with patch(
                "voicecli.nats.stt_adapter.api.transcribe",
                side_effect=_gated_transcribe,
            ):
                with patch(
                    "voicecli.nats.stt_adapter.scoped_path",
                    side_effect=lambda rid, ext: tmp_path / f"{rid}.{ext}",
                ):
                    handle_task = asyncio.create_task(adapter.handle(msg, payload))
                    hb_task = asyncio.create_task(adapter._heartbeat_loop())  # type: ignore[attr-defined]
                    try:
                        await asyncio.wait_for(handle_task, timeout=5.0)
                    finally:
                        hb_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await hb_task

        asyncio.run(_run())

        # Assert — gate was tripped, proving target_heartbeats fired
        assert heartbeat_count >= target_heartbeats, (
            f"heartbeat loop did not fire enough: got {heartbeat_count}, "
            f"expected >= {target_heartbeats}"
        )

    # ------------------------------------------------------------------
    # Case 22: request_id length boundary — 127/128 accepted, 129 rejected
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Validation error code (issue fix/nats-tts-newlines)
    # ------------------------------------------------------------------

    def test_value_error_from_transcribe_yields_param_validation_failed(
        self, tmp_path: Path
    ) -> None:
        """ValueError from api.transcribe → param_validation_failed (not transcription_failed)."""
        _require_imports()
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-stt-valerr")

        import voicecli.nats.stt_adapter as _mod
        import voicecli.api as _api  # noqa: F401

        _mod.api = _api  # type: ignore[attr-defined]

        with patch(
            "voicecli.nats.stt_adapter.api.transcribe",
            side_effect=ValueError("invalid language: xx"),
        ):
            with _patch_scoped_path(tmp_path):
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
        # Arrange
        rid = "a" * rid_len
        adapter = _make_adapter(max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id=rid)

        with _patch_transcribe(_fake_result()) as mock_transcribe:
            with _patch_scoped_path(tmp_path):
                asyncio.run(adapter.handle(msg, payload))

        # Assert
        reply = msg.last_reply()
        if expected_ok:
            assert reply.get("error") is None
            assert reply["ok"] is True
        else:
            assert reply["ok"] is False
            assert reply["error"] == "malformed_request"
            mock_transcribe.assert_not_called()
