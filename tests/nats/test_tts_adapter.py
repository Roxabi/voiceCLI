"""RED-phase tests for TtsNatsAdapter (issue #42 T6).

All 12 cases FAIL until voicecli.nats.tts_adapter is implemented.
The top-level imports are wrapped so pytest --collect-only works even before
the voicecli.nats package exists; individual tests will fail on ImportError.
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
    from voicecli.nats.config import _resolve_engine
    from voicecli.nats.tempdir import scoped_path  # noqa: F401
    from voicecli.nats.tts_adapter import TtsNatsAdapter

    _IMPORT_ERROR: ImportError | None = None
except ImportError as _e:
    _IMPORT_ERROR = _e
    TtsNatsAdapter = None  # type: ignore[assignment,misc]
    _resolve_engine = None  # type: ignore[assignment]
    scoped_path = None  # type: ignore[assignment]


def _require_imports() -> None:
    """Call at the top of every test; raises if the module is not yet implemented."""
    if _IMPORT_ERROR is not None:
        pytest.fail(f"voicecli.nats not yet implemented (RED): {_IMPORT_ERROR}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Canonical message stand-in lives in tests/nats/_fakes.py — aliased here so
# every existing MockMsg() call site keeps working unchanged.
from _fakes import FakeMsg as MockMsg  # noqa: E402
from _fakes import FakeNatsConn  # noqa: E402


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
# Test class
# ---------------------------------------------------------------------------


class TestTtsNatsAdapter:
    def test_handle_success_publishes_adr044_reply(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id="req-001")

        def _patched_scoped_path(request_id: str, ext: str) -> Path:
            return tmp_path / f"{request_id}.{ext}"

        with patch(
            "voicecli.engine._get_registry", return_value={"mock": _stub_engine_factory(tmp_path)}
        ):
            with patch("voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path):
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
        assert reply["mime_type"] == "audio/wav"
        assert "audio_b64" in reply
        base64.b64decode(reply["audio_b64"])  # must not raise
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
            "voicecli.engine._get_registry", return_value={"mock": _stub_engine_factory(tmp_path)}
        ):
            with patch("voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path):
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
            "voicecli.engine._get_registry", return_value={"mock": _stub_engine_factory(tmp_path)}
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
            "voicecli.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path, raises=RuntimeError("model OOM"))},
        ):
            with patch("voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path):
                asyncio.run(adapter.handle(msg, payload))

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "synthesis_failed"

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
            "voicecli.engine._get_registry", return_value={"mock": _stub_engine_factory(tmp_path)}
        ):
            with patch("voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path):
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
            "voicecli.engine._get_registry", return_value={"mock": _stub_engine_factory(tmp_path)}
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
            "voicecli.engine._get_registry", return_value={"mock": _stub_engine_factory(tmp_path)}
        ):
            asyncio.run(_run())

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "capacity_exceeded"

    def test_synthesized_wav_written_with_mode_0o600(self, tmp_path: Path) -> None:
        """Issue #60: synthesized WAV must be 0o600 before read_bytes, not 0o644.

        Intercepts base64.b64encode to snapshot the file mode at the exact moment
        _run_synthesis reads the finished WAV — the adapter's finally block removes
        the file before handle() returns, so a post-hoc stat would see nothing.
        """
        _require_imports()
        import base64 as _b64
        import os
        import stat as _stat

        request_id = "req-mode-0600"
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        payload = _valid_payload(request_id=request_id)

        wav_bytes = (
            b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
            b"\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00"
            b"\x02\x00\x10\x00data\x00\x00\x00\x00"
        )
        out_path = tmp_path / f"{request_id}.wav"

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        def _fake_generate(*args, **kwargs):
            out = Path(kwargs["output"])
            out.write_bytes(wav_bytes)
            # Simulate the real umask=0o022 leak — force 0o644 so the chmod in
            # _run_synthesis is what closes the gap, not test-harness luck.
            out.chmod(0o644)
            return None

        observed: dict[str, int] = {}
        real_b64encode = _b64.b64encode

        def _sniff(buf: bytes) -> bytes:
            if out_path.exists():
                observed["mode"] = _stat.S_IMODE(os.stat(out_path).st_mode)
            return real_b64encode(buf)

        with (
            patch("voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path),
            patch("voicecli.nats.tts_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
            patch("voicecli.nats.tts_adapter.base64.b64encode", side_effect=_sniff),
        ):
            asyncio.run(adapter.handle(msg, payload))

        assert msg.last_reply()["ok"] is True
        assert "mode" in observed, "base64.b64encode was never called — sniff never ran"
        assert observed["mode"] == 0o600, (
            f"synthesized WAV mode at read_bytes was "
            f"{oct(observed['mode'])}, expected 0o600 (issue #60)"
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
            "voicecli.engine._get_registry", return_value={"mock": _stub_engine_factory(tmp_path)}
        ):
            with patch("voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path):
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
            "voicecli.engine._get_registry",
            return_value={"mock": _stub_engine_factory(tmp_path, raises=RuntimeError("kaboom"))},
        ):
            with patch("voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path):
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

            async def _fake_publish(subject: str, data: bytes) -> None:
                nonlocal heartbeat_count
                if "heartbeat" in subject:
                    heartbeat_count += 1
                    if heartbeat_count >= target_heartbeats:
                        gate.set()

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
                "voicecli.engine._get_registry",
                return_value={"mock": _stub_engine_factory(tmp_path, gate=gate)},
            ):
                with patch(
                    "voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path
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
            "voicecli.engine._get_registry", return_value={"mock": _stub_engine_factory(tmp_path)}
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
            "voicecli.engine._get_registry", return_value={"mock": _stub_engine_factory(tmp_path)}
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
            "voicecli.engine._get_registry", return_value={"mock": _stub_engine_factory(tmp_path)}
        ):
            asyncio.run(adapter.handle(msg, payload))

        # Assert
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
            patch("voicecli.nats.tts_adapter._engine_available") as mock_avail,
            patch(
                "voicecli.engine._get_registry",
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
            patch("voicecli.nats.tts_adapter._engine_available") as mock_avail,
            patch(
                "voicecli.engine._get_registry",
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
            patch("voicecli.nats.tts_adapter._engine_available") as mock_avail,
            patch(
                "voicecli.engine._get_registry",
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
            patch("voicecli.nats.tts_adapter._engine_available") as mock_avail,
            patch(
                "voicecli.engine._get_registry",
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
            patch("voicecli.nats.tts_adapter._engine_available", return_value=False) as mock_avail,
            patch(
                "voicecli.engine._get_registry",
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
            patch("voicecli.nats.tts_adapter._engine_available", return_value=True) as mock_avail,
            patch(
                "voicecli.engine._get_registry",
                return_value={"mock": _stub_engine_factory(tmp_path)},
            ),
            patch("voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path),
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
            patch("voicecli.nats.tts_adapter._engine_available", return_value=True) as mock_avail,
            patch(
                "voicecli.engine._get_registry",
                return_value={"mock": _stub_engine_factory(tmp_path)},
            ),
            patch("voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path),
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
            # Write a 1-byte stub so base64 + waveform steps succeed.
            out = kwargs.get("output")
            if out is not None:
                Path(out).write_bytes(b"\x00")
            return None

        with (
            patch("voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path),
            patch("voicecli.nats.tts_adapter._engine_available", return_value=True),
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
                raise ValueError("unsupported language: zz")
            out = kwargs.get("output")
            if out is not None:
                Path(out).write_bytes(b"\x00")
            return None

        with (
            patch("voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path),
            patch("voicecli.nats.tts_adapter._engine_available", return_value=True),
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
        # No fallback_language provided → ValueError bubbles up as synthesis_failed.
        payload = _valid_payload(request_id="req-nofb") | {"language": "zz"}

        calls = 0

        def _patched_scoped_path(rid: str, ext: str) -> Path:
            return tmp_path / f"{rid}.{ext}"

        def _fake_generate(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise ValueError("unsupported language: zz")

        with (
            patch("voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path),
            patch("voicecli.nats.tts_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        assert calls == 1
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "synthesis_failed"

    def test_fallback_language_skipped_when_matches_primary(self, tmp_path: Path) -> None:
        _require_imports()
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
        _setup_adapter(adapter, msg)
        # fallback == primary → no retry, error is surfaced immediately.
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
            raise ValueError("unsupported language: en")

        with (
            patch("voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path),
            patch("voicecli.nats.tts_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        assert calls == 1
        assert msg.last_reply()["ok"] is False

    def test_waveform_b64_populated_on_success(self, tmp_path: Path) -> None:
        _require_imports()
        # Real WAV so the waveform helper can decode frames.
        import wave as _wave

        wav_path = tmp_path / "req-wf.wav"

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
            patch("voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path),
            patch("voicecli.nats.tts_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is True
        assert "waveform_b64" in reply
        assert len(base64.b64decode(reply["waveform_b64"])) == 256
        assert not wav_path.exists()  # cleanup still runs

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
            patch("voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path),
            patch("voicecli.nats.tts_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is True
        assert "waveform_b64" not in reply

    def test_existing_five_fields_still_forwarded(self, tmp_path: Path) -> None:
        _require_imports()
        # Regression check — the V1 field set must keep flowing after #47.
        payload = _valid_payload() | {
            "language": "en",
            "voice": "alice",
            "speed": 1.1,
            "exaggeration": 0.7,
            "cfg_weight": 0.5,
        }
        reply, _args, kwargs = self._run_with_capture(tmp_path, payload)
        assert reply["ok"] is True
        assert kwargs.get("language") == "en"
        assert kwargs.get("voice") == "alice"
        assert kwargs.get("speed") == 1.1
        assert kwargs.get("exaggeration") == 0.7
        assert kwargs.get("cfg_weight") == 0.5

    # ------------------------------------------------------------------
    # Chunked output — chatterbox writes {stem}_NNN.wav + .done
    # ------------------------------------------------------------------

    def _make_silent_wav_bytes(self, n_frames: int = 2205) -> bytes:
        """Build a minimal silent WAV (22050 Hz, 16-bit, mono)."""
        import struct as _struct
        import wave as _wave
        import io

        buf = io.BytesIO()
        with _wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(b"\x00\x00" * n_frames)
        return buf.getvalue()

    def test_chunked_output_single_chunk_succeeds(self, tmp_path: Path) -> None:
        """Engine writes {stem}_001.wav + {stem}.done — adapter concatenates and encodes."""
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
            patch("voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path),
            patch("voicecli.nats.tts_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is True, f"expected ok=True, got: {reply}"
        assert reply["mime_type"] == "audio/wav"
        assert "audio_b64" in reply
        decoded = base64.b64decode(reply["audio_b64"])
        # Decoded bytes must be a valid WAV (RIFF header)
        assert decoded[:4] == b"RIFF"
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
            patch("voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path),
            patch("voicecli.nats.tts_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is True, f"expected ok=True, got: {reply}"
        # duration_ms must reflect both chunks (≥ 200 ms of silence)
        assert reply["duration_ms"] >= 200
        decoded = base64.b64decode(reply["audio_b64"])
        assert decoded[:4] == b"RIFF"
        # All chunk files and .done must be cleaned up
        for i in (1, 2):
            assert not (tmp_path / f"{request_id}_{i:03d}.wav").exists()
        assert not (tmp_path / f"{request_id}.done").exists()

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
            patch("voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path),
            patch("voicecli.nats.tts_adapter._engine_available", return_value=True),
            patch("voicecli.api.generate", side_effect=_fake_generate),
        ):
            asyncio.run(adapter.handle(msg, payload))

        reply = msg.last_reply()
        assert reply["ok"] is True
        decoded = base64.b64decode(reply["audio_b64"])
        assert decoded[:4] == b"RIFF"
