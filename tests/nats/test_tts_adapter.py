"""RED-phase tests for TtsNatsAdapter (issue #42 T6).

All 12 cases FAIL until voicecli.nats.tts_adapter is implemented.
The top-level imports are wrapped so pytest --collect-only works even before
the voicecli.nats package exists; individual tests will fail on ImportError.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Lazy import shim — lets --collect-only succeed in RED phase
# ---------------------------------------------------------------------------

try:
    from voicecli.nats.reply import build_reply  # noqa: F401
    from voicecli.nats.tempdir import scoped_path  # noqa: F401
    from voicecli.nats.tts_adapter import TtsNatsAdapter, _resolve_engine

    _IMPORT_ERROR: ImportError | None = None
except ImportError as _e:
    _IMPORT_ERROR = _e
    TtsNatsAdapter = None  # type: ignore[assignment,misc]
    _resolve_engine = None  # type: ignore[assignment]
    build_reply = None  # type: ignore[assignment]
    scoped_path = None  # type: ignore[assignment]


def _require_imports() -> None:
    """Call at the top of every test; raises if the module is not yet implemented."""
    if _IMPORT_ERROR is not None:
        pytest.fail(f"voicecli.nats not yet implemented (RED): {_IMPORT_ERROR}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockMsg:
    """Minimal NATS message stand-in: carries .reply, records respond() calls."""

    def __init__(self, reply_subject: str = "_INBOX.test") -> None:
        self.reply = reply_subject
        self._published: list[bytes] = []

    async def respond(self, data: bytes) -> None:
        self._published.append(data)

    def last_reply(self) -> dict:
        assert self._published, "No reply published"
        return json.loads(self._published[-1])


def _valid_payload(
    *,
    request_id: str = "req-001",
    text: str = "Hello world",
    engine: str = "mock",
    contract_version: str = "1",
) -> dict:
    return {
        "contract_version": contract_version,
        "request_id": request_id,
        "text": text,
        "engine": engine,
    }


def _stub_engine_factory(
    tmp_path: Path,
    *,
    raises: Exception | None = None,
    sleep_s: float = 0.0,
):
    """Return a class (not instance) for injection into the engine registry.

    generate() writes a 1-byte WAV stub, or raises/sleeps per kwargs.
    """

    class _FakeEngine:
        name = "mock"

        def generate(self, text: str, voice, output_path: Path, **kwargs) -> Path:
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
        assert reply["mime_type"] == "audio/wav"
        assert "audio_b64" in reply
        base64.b64decode(reply["audio_b64"])  # must not raise
        assert isinstance(reply.get("duration_ms"), (int, float))

    def test_handle_unknown_engine_returns_engine_unavailable(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
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

    def test_temp_file_cleaned_up_on_success(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange
        request_id = "req-clean-ok"
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
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
        _require_imports()
        # Arrange — slow engine (1.5 s); heartbeat every 0.3 s → expect ≥ 1 heartbeat
        heartbeat_calls: list[float] = []

        async def _run() -> None:
            adapter = TtsNatsAdapter(
                default_engine="mock",
                max_concurrent=1,
                heartbeat_interval=0.3,
            )
            msg = MockMsg()
            payload = _valid_payload(request_id="req-hb")

            async def _fake_publish(subject: str, data: bytes) -> None:
                if "heartbeat" in subject:
                    heartbeat_calls.append(time.monotonic())

            def _patched_scoped_path(rid: str, ext: str) -> Path:
                return tmp_path / f"{rid}.{ext}"

            adapter._nats_publish = _fake_publish  # type: ignore[attr-defined]

            stop = asyncio.Event()

            with patch(
                "voicecli.engine._get_registry",
                return_value={"mock": _stub_engine_factory(tmp_path, sleep_s=1.5)},
            ):
                with patch(
                    "voicecli.nats.tts_adapter.scoped_path", side_effect=_patched_scoped_path
                ):
                    handle_task = asyncio.create_task(adapter.handle(msg, payload))
                    hb_task = asyncio.create_task(adapter._heartbeat_loop(stop))  # type: ignore[attr-defined]

                    await asyncio.wait_for(handle_task, timeout=5.0)
                    stop.set()
                    await asyncio.wait_for(hb_task, timeout=1.0)

        asyncio.run(_run())

        # Assert — heartbeat fired at least once during the inference window
        assert len(heartbeat_calls) >= 1

    def test_path_traversal_request_id_returns_malformed_request(self, tmp_path: Path) -> None:
        _require_imports()
        # Arrange — request_id contains path traversal
        adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
        msg = MockMsg()
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
