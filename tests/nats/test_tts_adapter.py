# pyright: reportOptionalCall=false, reportInvalidTypeForm=false
"""Unit tests for TtsNatsAdapter (issue #42 T6, updated #144 T17).

T17: Updated for V2 BlobRef contract — mocks get_blobstore().put; asserts
TtsResponse carries blob_ref. New test: BlobStore.put raises →
adapter publishes error envelope with error="audio_store_failed".

All success-path tests inject a _FakeBlobStore via the autouse fixture so that
the runner's get_blobstore().put() call returns a contract-compatible BlobRef dict.

Pyright directives at top: optional-import pattern (TtsNatsAdapter = None when
deps missing) is intentional — _require_imports() gates at runtime. Pyright
can't narrow through the gate so we silence the resulting OptionalCall +
InvalidTypeForm noise file-wide.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Lazy import shim — lets --collect-only succeed in RED phase
# ---------------------------------------------------------------------------

try:
    from voicecli.api import ParamValidationError
    from voicecli.adapters.nats.config import _resolve_engine
    from voicecli.adapters.nats.tempdir import scoped_path  # noqa: F401
    from voicecli.adapters.nats.synthesize_adapter import TtsNatsAdapter

    _IMPORT_ERROR: ImportError | None = None
except ImportError as _e:
    _IMPORT_ERROR = _e
    ParamValidationError = ValueError  # type: ignore[assignment,misc]
    TtsNatsAdapter = None  # type: ignore[assignment,misc]
    _resolve_engine = None  # type: ignore[assignment]
    scoped_path = None  # type: ignore[assignment]


def _require_imports() -> None:
    """Call at the top of every test; raises if the module is not yet implemented."""
    if _IMPORT_ERROR is not None:
        pytest.fail(f"voicecli.adapters.nats not yet implemented (RED): {_IMPORT_ERROR}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Canonical message stand-in lives in tests/nats/_fakes.py — aliased here so
# every existing MockMsg() call site keeps working unchanged.
from tests.nats._fakes import _BLOBSTORE_PATCH_PATH, _FakeBlobRef, FakeMsg as MockMsg, FakeNatsConn  # noqa: E402


def _setup_adapter(adapter: TtsNatsAdapter, msg: MockMsg) -> None:
    """Set up adapter with mock NATS connection for testing.

    The SDK's reply() method requires _nc to be set. This helper
    sets up a FakeNatsConn that forwards publishes to msg.respond().
    """
    adapter._nc = FakeNatsConn(msg)  # noqa: E402


def _valid_payload(
    *,
    request_id: str = "req-001",
    text: str = "Hello world",
    engine: str = "mock",
    contract_version: str = "1",
    trace_id: str | None = "test-trace-001",
) -> dict:
    payload = {
        "contract_version": contract_version,
        "request_id": request_id,
        "text": text,
        "engine": engine,
    }
    if trace_id is not None:
        payload["trace_id"] = trace_id
    return payload


def _stub_engine_factory(
    tmp_path: Path,
    *,
    raises: Exception | None = None,
    sleep_s: float = 0.0,
    gate: "threading.Event | None" = None,
    gate_timeout_s: float = 5.0,
):
    """Return a class (not instance) for injection into the engine registry.

    generate() writes a 1-byte WAV stub, or raises/sleeps per kwargs.

    If ``gate`` is set, ``generate()`` blocks until the gate is set (or
    ``gate_timeout_s`` elapses). This lets heartbeat tests hold the engine
    open until the loop proves liveness via an event, instead of relying on
    wall-clock bounds.
    """

    class _FakeEngine:
        name = "mock"

        def generate(self, text: str, voice, output_path: Path, **kwargs) -> Path:
            if gate is not None:
                gate.wait(timeout=gate_timeout_s)
            if sleep_s:
                time.sleep(sleep_s)
            if raises:
                raise raises
            output_path.write_bytes(b"\x00")
            return output_path

        def clone(self, text, ref_audio, output_path, ref_text=None, **kwargs) -> Path:
            output_path.write_bytes(b"\x00")
            return output_path

        def list_voices(self) -> list[str]:
            return []

    return _FakeEngine


# ---------------------------------------------------------------------------
# BlobStore fake — injected into every test via autouse fixture
# ---------------------------------------------------------------------------


class _FakeBlobStore:
    """Async fake BlobStore for adapter tests.

    Returned by the autouse fake_blobstore fixture.
    """

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
            content_hash="testkey",
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


@pytest.fixture(autouse=True)
def _inject_fake_blobstore(monkeypatch):  # pyright: ignore[reportUnusedFunction]
    """Inject a default _FakeBlobStore for every test in this module.

    Tests that need to control put() behaviour (e.g. raise_on_put) create their
    own _FakeBlobStore and patch blobs.get_blobstore directly in the test body.
    """
    store = _FakeBlobStore()
    monkeypatch.setattr(
        _BLOBSTORE_PATCH_PATH,
        lambda: store,
    )
    return store


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestTtsNatsAdapter:
    def test_handle_success_publishes_v2_reply_with_blob_ref(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-001")

        def _patched_scoped_path(request_id: str, ext: str) -> Path:
            return tmp_path / f"{request_id}.{ext}"

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path)},
        ):
            with patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ):
                asyncio.run(adapter.handle(msg, payload))

        # Assert — V2 response carries blob_ref
        reply = msg.last_reply()
        assert reply["ok"] is True
        assert reply["contract_version"] == "1"
        assert reply["request_id"] == "req-001"
        assert reply["trace_id"] == "test-trace-001"
        from datetime import datetime as _dt

        _iat = _dt.fromisoformat(reply["issued_at"])
        assert _iat.tzinfo is not None
        assert reply["mime_type"] == "audio/wav"
        # V2: blob_ref present
        assert "blob_ref" in reply
        assert isinstance(reply["blob_ref"], dict)
        assert (
            reply["blob_ref"]["store_key"]
            == "sha256:abc123abc123abc123abc123abc123abc123abc123abc123abc123abc123abc1"
        )
        assert isinstance(reply.get("duration_ms"), (int, float))

    def test_reply_uses_unknown_trace_id_when_absent(self, tmp_path: Path) -> None:
        _require_imports()
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-notrace", trace_id=None)

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path)},
        ):
            with patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ):
                asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is True
        assert reply["trace_id"] == "unknown"

    def test_handle_unknown_engine_returns_engine_unavailable(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(engine="ghost-engine")

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path)},
        ):
            asyncio.run(adapter.handle(msg, payload))

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "engine_unavailable"

    def test_handle_engine_raises_returns_synthesis_failed(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-boom")

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path, raises=RuntimeError("model OOM"))},
        ):
            with patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ):
                asyncio.run(adapter.handle(msg, payload))

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "synthesis_failed"

    def test_blobstore_put_raises_returns_audio_store_failed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """BlobStore.put raises → adapter publishes error envelope with audio_store_failed.

        Negative-test: if the inner try/except around get_blobstore().put() is removed,
        the exception propagates to the outer handler and yields 'synthesis_failed'
        instead of 'audio_store_failed'. This test fails if that distinction is lost.
        """
        _require_imports()
        # Arrange — override the autouse fake with a raising one
        failing_store = _FakeBlobStore(raise_on_put=ConnectionError("blobstore down"))
        monkeypatch.setattr(
            _BLOBSTORE_PATCH_PATH,
            lambda: failing_store,
        )
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-storefail")

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path)},
        ):
            with patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ):
                asyncio.run(adapter.handle(msg, payload))

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "audio_store_failed", (
            f"expected 'audio_store_failed', got {reply['error']!r}; "
            "removing the blobstore guard would yield 'synthesis_failed'"
        )

    def test_defensive_contract_version_999_is_handled(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange — contract_version "999" must NOT short-circuit (ADR-044 defensive read).
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-999", contract_version="999")

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path)},
        ):
            with patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ):
                asyncio.run(adapter.handle(msg, payload))

        # Assert — succeeds normally, reply stamps contract_version "1"
        reply = msg.last_reply()
        assert reply["ok"] is True
        assert reply["contract_version"] == "1"

    def test_missing_request_id_returns_malformed_request(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange — no request_id in payload
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = {"text": "Hello", "engine": "mock"}

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path)},
        ):
            asyncio.run(adapter.handle(msg, payload))

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"
        assert reply["request_id"] == ""
        assert "trace_id" in reply
        assert "issued_at" in reply

    def test_max_concurrent_default_is_1_for_tts(self) -> None:
        _require_imports()
        # Arrange + Act
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)

        # Assert — semaphore initialized with value 1
        sem = adapter._sem  # type: ignore[attr-defined]
        assert isinstance(sem, asyncio.Semaphore)

        async def _check() -> int:
            return sem._value  # noqa: SLF001

        value = asyncio.run(_check())
        assert value == 1

    def test_reject_when_full_returns_capacity_exceeded(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1, reject_when_full=True)
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

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path)},
        ):
            asyncio.run(_run())

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "capacity_exceeded"

    def test_synthesized_wav_written_with_mode_0o600(self, tmp_path: Path) -> None:
        """Issue #60: synthesized WAV must be 0o600 before BlobStore.put, not 0o644.

        Intercepts the fake BlobStore's put() to snapshot the file mode at the exact
        moment the runner reads the finished WAV — the adapter's finally block removes
        the file before handle() returns.
        """
        _require_imports()
        import os
        import stat as _stat

        request_id = "req-mode-0600"
        observed: dict[str, int] = {}

        # Override autouse fake with a sniffing one
        class _SniffingBlobStore(_FakeBlobStore):
            async def put(self, data: bytes, *, mime: str, source: str, **kw) -> _FakeBlobRef:
                out = tmp_path / f"{request_id}.wav"
                if out.exists():
                    observed["mode"] = _stat.S_IMODE(os.stat(out).st_mode)
                return await super().put(data, mime=mime, source=source, **kw)

        # This test patches blobs.get_blobstore directly so the sniffing store is used.
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id=request_id)

        wav_bytes = (
            b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
            b"\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00"
            b"\x02\x00\x10\x00data\x00\x00\x00\x00"
        )

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        def _fake_generate(*args, **kwargs):
            out = Path(kwargs["output"])
            out.write_bytes(wav_bytes)
            # Simulate the real umask=0o022 leak — force 0o644 so the chmod in
            # _run_synthesis is what closes the gap, not test-harness luck.
            out.chmod(0o644)
            return None

        sniffing_store = _SniffingBlobStore()
        with (
            patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ),
            patch("voicecli.adapters.nats.synthesize_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
            patch(
                _BLOBSTORE_PATCH_PATH,
                return_value=sniffing_store,
            ),
        ):
            asyncio.run(adapter.handle(msg, payload))

        assert msg.last_reply()["ok"] is True
        assert "mode" in observed, "BlobStore.put() was never called — sniff never ran"
        assert observed["mode"] == 0o600, (
            f"synthesized WAV mode at put() was {oct(observed['mode'])}, expected 0o600 (issue #60)"
        )

    def test_temp_file_cleaned_up_on_success(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        request_id = "req-clean-ok"
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id=request_id)
        temp_file = tmp_path / f"{request_id}.wav"

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path)},
        ):
            with patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ):
                asyncio.run(adapter.handle(msg, payload))

        # Assert — temp file removed after successful reply
        assert not temp_file.exists()

    def test_temp_file_cleaned_up_on_failure(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        request_id = "req-clean-fail"
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id=request_id)
        temp_file = tmp_path / f"{request_id}.wav"

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            p = tmp_path / f"{rid}.{ext}"
            p.write_bytes(b"\x00")  # pre-create so cleanup has something to remove
            return p

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path, raises=RuntimeError("kaboom"))},
        ):
            with patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ):
                asyncio.run(adapter.handle(msg, payload))

        # Assert — temp file removed even when engine raises
        assert not temp_file.exists()

    def test_heartbeat_continues_during_inference(self, tmp_path: Path) -> None:
        """The heartbeat loop keeps firing while ``handle()`` is in-flight.

        Deterministic gate: the stub engine blocks in ``generate()`` until it
        has observed at least ``target_heartbeats`` heartbeat publishes. The
        heartbeat fake-publish sets a ``threading.Event`` once the count is
        reached, unblocking the engine. No wall-clock bounds — if the loop
        ever stops firing, the engine blocks indefinitely and ``wait_for``
        below times out with a clear failure.

        Floor of ``target_heartbeats = 3`` catches the "fires once at entry"
        degradation. No ceiling is asserted: a fast runner is not a bug.
        """
        _require_imports()

        gate = threading.Event()
        heartbeat_count = 0
        target_heartbeats = 3

        async def _run() -> None:
            nonlocal heartbeat_count
            adapter = TtsNatsAdapter(
                default_engine="mock",
                max_concurrent=1,
                heartbeat_interval=0.05,  # fast enough to accumulate 3 quickly
            )
            msg = MockMsg()
            _setup_adapter(adapter, msg)
            payload = _valid_payload(request_id="req-hb")

            def _patched_scoped_path(rid: str, ext: str) -> Path:
                return tmp_path / f"{rid}.{ext}"

            original_publish = adapter._nc.publish  # type: ignore[union-attr]

            async def _counting_publish(subject: str, data: bytes) -> None:
                nonlocal heartbeat_count
                if "heartbeat" in subject:
                    heartbeat_count += 1
                    if heartbeat_count >= target_heartbeats:
                        gate.set()
                await original_publish(subject, data)

            adapter._nc.publish = _counting_publish  # type: ignore[union-attr, method-assign]

            with patch(
                "voicecli.engines.engine._get_registry",
                return_value={"mock": _stub_engine_factory(tmp_path, gate=gate)},
            ):
                with patch(
                    "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                    side_effect=_patched_scoped_path,
                ):
                    handle_task = asyncio.create_task(adapter.handle(msg, payload))
                    hb_task = asyncio.create_task(adapter._heartbeat_loop())  # type: ignore[attr-defined]
                    try:
                        # If the loop stops firing, gate never sets → handle_task
                        # blocks → wait_for times out with a clean failure message
                        await asyncio.wait_for(handle_task, timeout=5.0)
                    finally:
                        hb_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await hb_task

        asyncio.run(_run())

        # Assert — gate was tripped, proving at least target_heartbeats fired
        assert heartbeat_count >= target_heartbeats, (
            f"heartbeat loop did not fire enough: got {heartbeat_count}, "
            f"expected >= {target_heartbeats}"
        )

    def test_path_traversal_request_id_returns_malformed_request(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange — request_id contains path traversal
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="../escape")

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path)},
        ):
            asyncio.run(adapter.handle(msg, payload))

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"

    def test_missing_text_returns_malformed_request(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange — no text in payload
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = {"contract_version": "1", "request_id": "req-notext", "engine": "mock"}

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path)},
        ):
            asyncio.run(adapter.handle(msg, payload))

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"

    def test_non_string_text_returns_malformed_request(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange — text is not a string
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = {
            "contract_version": "1",
            "request_id": "req-badtext",
            "text": 42,
            "engine": "mock",
        }

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path)},
        ):
            asyncio.run(adapter.handle(msg, payload))

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"

    def test_string_speed_accepted(self, tmp_path: Path) -> None:
        """String speed (e.g. 'ultra fast speaking') must be ACCEPTED — not malformed_request.

        speed is a free-text style hint per roxabi-contracts TtsRequest.speed: str | None.
        The old float-only validation was a local type drift that caused every lyra agent
        TTS request with a string speed to be rejected.
        """
        _require_imports()
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload()
        payload["speed"] = "ultra fast speaking"

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path)},
        ):
            with patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ):
                asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is True, f"string speed should be accepted, got: {reply}"

    def test_non_str_speed_returns_malformed_request(self, tmp_path: Path) -> None:
        """Non-str speed (int, float, list, …) must still be rejected as malformed_request."""
        _require_imports()
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload()
        payload["speed"] = 1.5  # numeric — no longer valid

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path)},
        ):
            asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"

    def test_malformed_chunked_type(self, tmp_path: Path) -> None:
        _require_imports()
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload()
        payload["chunked"] = "yes"

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path)},
        ):
            asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"

    def test_engine_env_var_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _require_imports()
        # Arrange
        monkeypatch.setenv("VOICECLI_ENGINE", "qwen-fast")
        monkeypatch.delenv("LYRA_TTS_ENGINE", raising=False)

        # Act
        resolved = _resolve_engine()

        # Assert
        assert resolved == "qwen-fast"

    # ------------------------------------------------------------------
    # V4 — Engine token format validation (T16)
    # ------------------------------------------------------------------

    def test_handle_rejects_malformed_engine_space(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange — engine with a space fails validate_nats_token
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-space", engine="a b")

        with (
            patch("voicecli.adapters.nats.synthesize_adapter._engine_available") as mock_avail,
            patch(
                "voicecli.engines.engine._get_registry",
                return_value={"mock": _stub_engine_factory(tmp_path)},
            ),
        ):
            asyncio.run(adapter.handle(msg, payload))
            mock_avail.assert_not_called()

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"

    def test_handle_rejects_malformed_engine_wildcard(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange — engine with wildcard chars fails validate_nats_token
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-wild", engine="*.tts")

        with (
            patch("voicecli.adapters.nats.synthesize_adapter._engine_available") as mock_avail,
            patch(
                "voicecli.engines.engine._get_registry",
                return_value={"mock": _stub_engine_factory(tmp_path)},
            ),
        ):
            asyncio.run(adapter.handle(msg, payload))
            mock_avail.assert_not_called()

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"

    def test_handle_rejects_malformed_engine_double_wildcard(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange — engine "**" fails validate_nats_token
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-dbl-wild", engine="**")

        with (
            patch("voicecli.adapters.nats.synthesize_adapter._engine_available") as mock_avail,
            patch(
                "voicecli.engines.engine._get_registry",
                return_value={"mock": _stub_engine_factory(tmp_path)},
            ),
        ):
            asyncio.run(adapter.handle(msg, payload))
            mock_avail.assert_not_called()

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"

    @pytest.mark.parametrize(
        "bad_engine",
        [" qwen", "qwen ", " ", "\tqwen", "a b c"],
        ids=["leading_space", "trailing_space", "single_space", "leading_tab", "inner_spaces"],
    )
    def test_handle_rejects_whitespace_in_engine(self, tmp_path: Path, bad_engine: str) -> None:
        _require_imports()
        # Arrange — any whitespace fails validate_nats_token (re.fullmatch on [A-Za-z0-9_.\-]+)
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-ws", engine=bad_engine)

        with (
            patch("voicecli.adapters.nats.synthesize_adapter._engine_available") as mock_avail,
            patch(
                "voicecli.engines.engine._get_registry",
                return_value={"mock": _stub_engine_factory(tmp_path)},
            ),
        ):
            asyncio.run(adapter.handle(msg, payload))
            mock_avail.assert_not_called()

        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"

    def test_handle_accepts_dotted_engine_name(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange — dots are allowed by [A-Za-z0-9_.\-]+; must reach _engine_available
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-dotted", engine="engine.with.dots")

        with (
            patch(
                "voicecli.adapters.nats.synthesize_adapter._engine_available", return_value=False
            ) as mock_avail,
            patch(
                "voicecli.engines.engine._get_registry",
                return_value={"mock": _stub_engine_factory(tmp_path)},
            ),
        ):
            asyncio.run(adapter.handle(msg, payload))
            # Must have passed the format gate and reached the registry check
            mock_avail.assert_called_once_with("engine.with.dots")

        reply = msg.last_reply()
        # Registry rejects it → engine_unavailable, NOT malformed_request
        assert reply["ok"] is False
        assert reply["error"] == "engine_unavailable"

    def test_handle_default_engine_when_engine_missing(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange — payload has no engine key; falls back to self.default_engine
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = {"contract_version": "1", "request_id": "req-noeng", "text": "hello"}

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        with (
            patch(
                "voicecli.adapters.nats.synthesize_adapter._engine_available", return_value=True
            ) as mock_avail,
            patch(
                "voicecli.engines.engine._get_registry",
                return_value={"mock": _stub_engine_factory(tmp_path)},
            ),
            patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ),
        ):
            asyncio.run(adapter.handle(msg, payload))
            mock_avail.assert_called_once_with("mock")

        # Assert — synthesis succeeded using the default engine; no error of any kind
        reply = msg.last_reply()
        assert reply["ok"] is True
        assert "error" not in reply

    def test_handle_default_engine_when_engine_empty(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange — engine="" is falsy; falls back to self.default_engine
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = {
            "contract_version": "1",
            "request_id": "req-empeng",
            "text": "hello",
            "engine": "",
        }

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        with (
            patch(
                "voicecli.adapters.nats.synthesize_adapter._engine_available", return_value=True
            ) as mock_avail,
            patch(
                "voicecli.engines.engine._get_registry",
                return_value={"mock": _stub_engine_factory(tmp_path)},
            ),
            patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ),
        ):
            asyncio.run(adapter.handle(msg, payload))
            mock_avail.assert_called_once_with("mock")

        # Assert — synthesis succeeded using the default engine; no error of any kind
        reply = msg.last_reply()
        assert reply["ok"] is True
        assert "error" not in reply

    def test_lyra_tts_engine_alias_works(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _require_imports()
        # Arrange — LYRA_TTS_ENGINE set, VOICECLI_ENGINE absent
        monkeypatch.setenv("LYRA_TTS_ENGINE", "qwen")
        monkeypatch.delenv("VOICECLI_ENGINE", raising=False)

        # Act
        resolved = _resolve_engine()

        # Assert
        assert resolved == "qwen"

    # ------------------------------------------------------------------
    # Issue #47 — Full ADR-044 TTS field coverage
    # ------------------------------------------------------------------

    def _run_with_capture(self, tmp_path: Path, payload: dict) -> tuple[dict, list, dict]:
        """Invoke handle() with api.generate patched; return (reply, args, kwargs)."""
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        captured: dict = {}

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        def _fake_generate(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            # Write a 1-byte stub so duration + waveform steps succeed.
            out = kwargs.get("output")
            if out is not None:
                Path(out).write_bytes(b"\x00")
            return None

        with (
            patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ),
            patch("voicecli.adapters.nats.synthesize_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        return msg.last_reply(), captured.get("args", ()), captured.get("kwargs", {})

    @pytest.mark.parametrize(
        "field,value",
        [
            ("accent", "british"),
            ("personality", "cheerful"),
            ("emotion", "excited"),
        ],
    )
    def test_forwards_instruct_parts(self, tmp_path: Path, field: str, value: str) -> None:
        _require_imports()
        payload = _valid_payload() | {field: value}
        reply, _args, kwargs = self._run_with_capture(tmp_path, payload)
        assert reply["ok"] is True
        assert kwargs.get(field) == value

    def test_forwards_chunked_true(self, tmp_path: Path) -> None:
        _require_imports()
        payload = _valid_payload() | {"chunked": True}
        reply, _args, kwargs = self._run_with_capture(tmp_path, payload)
        assert reply["ok"] is True
        assert kwargs.get("chunked") is True

    def test_forwards_chunked_false(self, tmp_path: Path) -> None:
        _require_imports()
        # Explicit False must still be forwarded (covers the `is not None` guard).
        payload = _valid_payload() | {"chunked": False}
        reply, _args, kwargs = self._run_with_capture(tmp_path, payload)
        assert reply["ok"] is True
        assert kwargs.get("chunked") is False

    @pytest.mark.parametrize(
        "field,value",
        [
            ("chunk_size", 500),
            ("segment_gap", 250),
            ("crossfade", 100),
        ],
    )
    def test_forwards_named_chunking_fields(self, tmp_path: Path, field: str, value: int) -> None:
        _require_imports()
        payload = _valid_payload() | {field: value}
        reply, _args, kwargs = self._run_with_capture(tmp_path, payload)
        assert reply["ok"] is True
        assert kwargs.get(field) == value

    def test_omits_unset_fields(self, tmp_path: Path) -> None:
        _require_imports()
        # Minimal payload — none of the optional engine kwargs should be forwarded.
        payload = _valid_payload()
        reply, _args, kwargs = self._run_with_capture(tmp_path, payload)
        assert reply["ok"] is True
        for field in (
            "accent",
            "personality",
            "emotion",
            "chunked",
            "chunk_size",
            "segment_gap",
            "crossfade",
            "language",
            "voice",
        ):
            assert field not in kwargs, f"{field} should not be forwarded when unset"

    def test_fallback_language_retry_on_value_error(self, tmp_path: Path) -> None:
        _require_imports()
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-fb") | {
            "language": "zz",
            "fallback_language": "en",
        }

        call_languages: list[str | None] = []

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        def _fake_generate(*args, **kwargs):
            lang = kwargs.get("language")
            call_languages.append(lang)
            if lang == "zz":
                raise ParamValidationError("unsupported language: zz")
            out = kwargs.get("output")
            if out is not None:
                Path(out).write_bytes(b"\x00")
            return None

        with (
            patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ),
            patch("voicecli.adapters.nats.synthesize_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        # Asserted: primary fails, fallback succeeds, reply is ok.
        assert call_languages == ["zz", "en"]
        assert msg.last_reply()["ok"] is True

    def test_fallback_language_no_retry_when_absent(self, tmp_path: Path) -> None:
        _require_imports()
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-nofb") | {"language": "zz"}

        calls = 0

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        def _fake_generate(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise ParamValidationError("unsupported language: zz")

        with (
            patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ),
            patch("voicecli.adapters.nats.synthesize_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        assert calls == 1
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "param_validation_failed"

    def test_fallback_language_skipped_when_matches_primary(self, tmp_path: Path) -> None:
        _require_imports()
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-same") | {
            "language": "en",
            "fallback_language": "en",
        }
        calls = 0

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        def _fake_generate(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise ParamValidationError("unsupported language: en")

        with (
            patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ),
            patch("voicecli.adapters.nats.synthesize_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        assert calls == 1
        assert msg.last_reply()["ok"] is False

    def test_waveform_b64_populated_on_success(self, tmp_path: Path) -> None:
        _require_imports()
        # Real WAV so the waveform helper can decode frames.
        import wave as _wave

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        def _fake_generate(*args, **kwargs):
            out = Path(kwargs["output"])
            with _wave.open(str(out), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(8000)
                # 400 ms of silence is enough to fill 256 amplitude buckets.
                wf.writeframes(b"\x00\x00" * 3200)
            return None

        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-wf")

        with (
            patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ),
            patch("voicecli.adapters.nats.synthesize_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is True
        # V2: waveform_b64 retained inline per contract
        assert "waveform_b64" in reply
        assert len(base64.b64decode(reply["waveform_b64"])) == 256
        # V2: blob_ref present
        assert "blob_ref" in reply
        # temp file removed by cleanup
        assert not (tmp_path / "req-wf.wav").exists()

    def test_waveform_b64_omitted_when_wav_unreadable(self, tmp_path: Path) -> None:
        _require_imports()
        # 1-byte stub is not a valid WAV → helper returns None → field omitted.
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-no-wf")

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        def _fake_generate(*args, **kwargs):
            Path(kwargs["output"]).write_bytes(b"\x00")
            return None

        with (
            patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ),
            patch("voicecli.adapters.nats.synthesize_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is True
        assert "waveform_b64" not in reply

    def test_existing_five_fields_still_forwarded(self, tmp_path: Path) -> None:
        _require_imports()
        # Regression check — the V1 field set must keep flowing after #47.
        # speed is a str per contract (free-text style hint, e.g. "ultra fast speaking").
        payload = _valid_payload() | {
            "language": "en",
            "voice": "alice",
            "speed": "ultra fast speaking",
            "exaggeration": 0.7,
            "cfg_weight": 0.5,
        }
        reply, _args, kwargs = self._run_with_capture(tmp_path, payload)
        assert reply["ok"] is True
        assert kwargs.get("language") == "en"
        assert kwargs.get("voice") == "alice"
        assert kwargs.get("speed") == "ultra fast speaking"
        assert kwargs.get("exaggeration") == 0.7
        assert kwargs.get("cfg_weight") == 0.5

    # ------------------------------------------------------------------
    # Chunked output — chatterbox writes {stem}_NNN.wav + .done
    # ------------------------------------------------------------------

    def _make_silent_wav_bytes(self, n_frames: int = 2205) -> bytes:
        """Build a minimal silent WAV (22050 Hz, 16-bit, mono)."""
        import io
        import wave as _wave

        buf = io.BytesIO()
        with _wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(b"\x00\x00" * n_frames)
        return buf.getvalue()

    def test_chunked_output_single_chunk_succeeds(self, tmp_path: Path) -> None:
        """Engine writes {stem}_001.wav + {stem}.done — adapter concatenates and stores."""
        _require_imports()
        request_id = "req-chunk1"
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id=request_id)
        wav_bytes = self._make_silent_wav_bytes()

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        def _fake_generate(*args, **kwargs):
            out = Path(kwargs["output"])
            # Write chunk file and done marker (mimics api._generate_chunked behaviour)
            chunk = out.parent / f"{out.stem}_001.wav"
            chunk.write_bytes(wav_bytes)
            done = out.with_suffix(".done")
            done.write_text("done\n")
            # out_path itself ({stem}.wav) is intentionally NOT written
            return None

        with (
            patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ),
            patch("voicecli.adapters.nats.synthesize_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is True, f"expected ok=True, got: {reply}"
        assert reply["mime_type"] == "audio/wav"
        # V2: blob_ref present
        assert "blob_ref" in reply
        # Chunk files and .done sentinel must be cleaned up
        assert not (tmp_path / f"{request_id}_001.wav").exists()
        assert not (tmp_path / f"{request_id}.done").exists()

    def test_chunked_output_multiple_chunks_concatenated(self, tmp_path: Path) -> None:
        """Engine writes {stem}_001.wav + {stem}_002.wav + {stem}.done — adapter
        concatenates all chunks and the resulting audio is longer than a single chunk."""
        _require_imports()
        request_id = "req-chunk2"
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id=request_id)
        chunk_frames = 2205  # 100 ms of silence per chunk @ 22050 Hz
        wav_bytes = self._make_silent_wav_bytes(chunk_frames)

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        def _fake_generate(*args, **kwargs):
            out = Path(kwargs["output"])
            for i in (1, 2):
                chunk = out.parent / f"{out.stem}_{i:03d}.wav"
                chunk.write_bytes(wav_bytes)
            done = out.with_suffix(".done")
            done.write_text("done\n")
            return None

        with (
            patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ),
            patch("voicecli.adapters.nats.synthesize_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is True, f"expected ok=True, got: {reply}"
        # duration_ms must reflect both chunks (≥ 200 ms of silence)
        assert reply["duration_ms"] >= 200
        # V2: blob_ref present
        assert "blob_ref" in reply
        # All chunk files and .done must be cleaned up
        for i in (1, 2):
            assert not (tmp_path / f"{request_id}_{i:03d}.wav").exists()
        assert not (tmp_path / f"{request_id}.done").exists()

    # ------------------------------------------------------------------
    # Newline stripping + validation error codes (issue fix/nats-tts-newlines)
    # ------------------------------------------------------------------

    def _run_synth_with_fake_generate(
        self,
        *,
        payload: dict,
        fake_generate,
        tmp_path: Path,
    ) -> dict:
        """Shared helper: run handle() with a caller-supplied fake generate.

        Returns the reply dict. Patches scoped_path and _engine_available so
        callers only need to supply the domain-specific fake.
        """

        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        with (
            patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ),
            patch("voicecli.adapters.nats.synthesize_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        return msg.last_reply()

    def test_text_with_newlines_accepted(self, tmp_path: Path) -> None:
        """Multi-paragraph text containing \\n is stripped and synthesis succeeds.

        The text passed to api.generate must contain neither \\n nor \\r (F20).
        """
        _require_imports()
        payload = _valid_payload(request_id="req-newlines", text="para 1.\n\npara 2.")
        captured: dict = {}

        def _fake_generate(*args, **kwargs):
            captured["text"] = args[0] if args else kwargs.get("text", "")
            out = kwargs.get("output")
            if out is not None:
                Path(out).write_bytes(b"\x00")
            return None

        reply = self._run_synth_with_fake_generate(
            payload=payload, fake_generate=_fake_generate, tmp_path=tmp_path
        )
        assert reply["ok"] is True
        assert "\n" not in captured["text"] and "\r" not in captured["text"]
        assert captured["text"] == "para 1.  para 2."  # 2 \n → 2 spaces

    def test_text_with_crlf_accepted(self, tmp_path: Path) -> None:
        """CRLF line endings (\\r\\n) are stripped and synthesis succeeds (F12).

        The text passed to api.generate must contain neither \\r nor \\n.
        """
        _require_imports()
        payload = _valid_payload(request_id="req-crlf", text="para 1.\r\npara 2.")
        captured: dict = {}

        def _fake_generate(*args, **kwargs):
            captured["text"] = args[0] if args else kwargs.get("text", "")
            out = kwargs.get("output")
            if out is not None:
                Path(out).write_bytes(b"\x00")
            return None

        reply = self._run_synth_with_fake_generate(
            payload=payload, fake_generate=_fake_generate, tmp_path=tmp_path
        )
        assert reply["ok"] is True
        assert "\n" not in captured["text"] and "\r" not in captured["text"]
        assert captured["text"] == "para 1. para 2."  # 1 \r\n → 1 space (NOT 2)

    def test_text_with_bare_cr_accepted(self, tmp_path: Path) -> None:
        """Bare \\r (no \\n) is stripped and synthesis succeeds.

        Guards the middle branch of the replace chain
        (.replace("\\r\\n", " ").replace("\\r", " ").replace("\\n", " ")) — if
        the bare-\\r branch were deleted, only this test would catch it.
        """
        _require_imports()
        payload = _valid_payload(request_id="req-bare-cr", text="para 1.\rpara 2.\r")
        captured: dict = {}

        def _fake_generate(*args, **kwargs):
            captured["text"] = args[0] if args else kwargs.get("text", "")
            out = kwargs.get("output")
            if out is not None:
                Path(out).write_bytes(b"\x00")
            return None

        reply = self._run_synth_with_fake_generate(
            payload=payload, fake_generate=_fake_generate, tmp_path=tmp_path
        )
        assert reply["ok"] is True
        assert "\r" not in captured["text"] and "\n" not in captured["text"]
        assert captured["text"] == "para 1. para 2. "

    def test_text_all_newlines_yields_malformed_request(self, tmp_path: Path) -> None:
        """Text consisting entirely of newlines is rejected after stripping (F14).

        After replacing \\n/\\r → space the text becomes whitespace-only.
        The adapter's post-strip blank-string guard fires and replies
        malformed_request before reaching api.generate.
        """
        _require_imports()
        payload = _valid_payload(request_id="req-allnl", text="\n\n\r\n\r")

        def _fake_generate(*args, **kwargs):
            raise AssertionError("api.generate must not be called for whitespace-only text")

        reply = self._run_synth_with_fake_generate(
            payload=payload, fake_generate=_fake_generate, tmp_path=tmp_path
        )
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"

    def test_fallback_language_also_fails_yields_param_validation_failed(
        self, tmp_path: Path
    ) -> None:
        """Both primary and fallback language raise ParamValidationError → param_validation_failed.

        The adapter catches the primary ParamValidationError, retries with the fallback
        language, and if the fallback also raises ParamValidationError it catches that too
        and replies param_validation_failed — consistent with the no-fallback path.
        """
        _require_imports()
        payload = _valid_payload(request_id="req-fb-fail") | {
            "language": "zz",
            "fallback_language": "xx",
        }
        call_languages: list[str | None] = []

        def _fake_generate(*args, **kwargs):
            lang = kwargs.get("language")
            call_languages.append(lang)
            if lang == "zz":
                raise ParamValidationError("zz unsupported")
            raise ParamValidationError("xx unsupported")

        reply = self._run_synth_with_fake_generate(
            payload=payload, fake_generate=_fake_generate, tmp_path=tmp_path
        )
        assert call_languages == ["zz", "xx"]
        assert reply["ok"] is False
        assert reply["error"] == "param_validation_failed"

    def test_value_error_from_generate_no_fallback_yields_param_validation_failed(
        self, tmp_path: Path
    ) -> None:
        """ParamValidationError from api.generate with no fallback → param_validation_failed.

        The exc message is NOT echoed to the wire — it stays in the structured log
        only (security: prevents leaking user-controlled input back over NATS).
        """
        _require_imports()
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-valerr")

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        def _fake_generate(*args, **kwargs):
            raise ParamValidationError("voice must not be empty")

        with (
            patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ),
            patch("voicecli.adapters.nats.synthesize_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "param_validation_failed"

    def test_fallback_language_retry_succeeds_not_validation_failed(self, tmp_path: Path) -> None:
        """ParamValidationError on primary + fallback_language set → retry succeeds → ok=True."""
        _require_imports()
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-fb-ok") | {
            "language": "zz",
            "fallback_language": "en",
        }

        call_languages: list[str | None] = []

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        def _fake_generate(*args, **kwargs):
            lang = kwargs.get("language")
            call_languages.append(lang)
            if lang == "zz":
                raise ParamValidationError("unsupported language: zz")
            out = kwargs.get("output")
            if out is not None:
                Path(out).write_bytes(b"\x00")
            return None

        with (
            patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ),
            patch("voicecli.adapters.nats.synthesize_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        assert call_languages == ["zz", "en"]
        reply = msg.last_reply()
        assert reply["ok"] is True
        assert "error" not in reply

    def test_non_chunked_output_unaffected(self, tmp_path: Path) -> None:
        """Engine writes {stem}.wav directly (no .done) — existing path unchanged."""
        _require_imports()
        request_id = "req-nochunk"
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id=request_id)
        wav_bytes = self._make_silent_wav_bytes()

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        def _fake_generate(*args, **kwargs):
            # Standard non-chunked: write directly to out_path
            out = Path(kwargs["output"])
            out.write_bytes(wav_bytes)
            return None

        with (
            patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ),
            patch("voicecli.adapters.nats.synthesize_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is True
        # V2: blob_ref present
        assert "blob_ref" in reply

    # ------------------------------------------------------------------
    # WorkerError structured error field — populated on all failure paths
    # ------------------------------------------------------------------

    def test_err_tts_sets_both_error_and_worker_error(self, tmp_path: Path) -> None:
        """Every error reply must carry both flat 'error' (code) and structured 'worker_error'."""
        _require_imports()
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-we-both")

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path, raises=RuntimeError("boom"))},
        ):
            with patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ):
                asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is False
        # Flat error field for back-compat
        assert reply["error"] == "synthesis_failed"
        # Structured worker_error must be present and consistent
        assert "worker_error" in reply
        assert reply["worker_error"]["code"] == "synthesis_failed"
        assert reply["worker_error"]["retryable"] is False

    def test_unknown_voice_returns_structured_unknown_voice_error(self, tmp_path: Path) -> None:
        """ValueError('Unknown voice ...') from engine → wire error='unknown_voice' + worker_error."""
        _require_imports()
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-unkv") | {"voice": "Cherry"}

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        def _fake_generate(*args, **kwargs):
            raise ValueError("Unknown voice 'Cherry'. Available: ['Alice', 'Bob']")

        with (
            patch(
                "voicecli.adapters.nats.synthesize_adapter.scoped_path",
                side_effect=_patched_scoped_path,
            ),
            patch("voicecli.adapters.nats.synthesize_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "unknown_voice"
        assert "worker_error" in reply
        we = reply["worker_error"]
        assert we["code"] == "unknown_voice"
        assert we["retryable"] is False
        assert "Cherry" in we["message"]
        assert we.get("detail") is not None
        assert "Available" in we["detail"]

    def test_malformed_request_reply_carries_worker_error(self, tmp_path: Path) -> None:
        """Malformed-request (missing request_id) reply carries structured worker_error."""
        _require_imports()
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = {"text": "Hello", "engine": "mock"}  # no request_id

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path)},
        ):
            asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"
        assert "worker_error" in reply
        assert reply["worker_error"]["code"] == "malformed_request"
        assert reply["worker_error"]["retryable"] is False

    def test_engine_unavailable_reply_carries_worker_error(self, tmp_path: Path) -> None:
        """engine_unavailable error reply carries structured worker_error."""
        _require_imports()
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(engine="ghost-engine")

        with patch(
            "voicecli.engines.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path)},
        ):
            asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "engine_unavailable"
        assert "worker_error" in reply
        assert reply["worker_error"]["code"] == "engine_unavailable"
