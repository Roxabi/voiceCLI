"""Tests for ``synthesize_via_nats`` — the client-side NATS TTS helper.

Mirror of ``test_stt_adapter`` style — pin happy + 4 error paths.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

from voicecli.adapters.nats import synthesize_client


class _FakeReply:
    def __init__(self, data: bytes) -> None:
        self.data = data


class _FakeNc:
    def __init__(
        self, reply_payload: bytes | None = None, raise_on_request: BaseException | None = None
    ) -> None:
        self._reply_payload = reply_payload
        self._raise_on_request = raise_on_request
        self.drained = False
        self.closed = False
        self.requests: list[tuple[str, bytes, float]] = []

    async def request(self, subject: str, payload: bytes, timeout: float):
        self.requests.append((subject, payload, timeout))
        if self._raise_on_request is not None:
            raise self._raise_on_request
        assert self._reply_payload is not None
        return _FakeReply(self._reply_payload)

    async def drain(self) -> None:
        self.drained = True

    async def close(self) -> None:
        self.closed = True


def _tts_response_ok(audio: bytes, request_id: str = "rid-1") -> bytes:
    return json.dumps(
        {
            "contract_version": "1.0",
            "trace_id": request_id,
            "issued_at": "2026-01-01T00:00:00Z",
            "ok": True,
            "request_id": request_id,
            "audio_b64": base64.b64encode(audio).decode("ascii"),
            "mime_type": "audio/wav",
            "duration_ms": 1234,
        }
    ).encode("utf-8")


def _tts_response_err(error: str, request_id: str = "rid-1") -> bytes:
    return json.dumps(
        {
            "contract_version": "1.0",
            "trace_id": request_id,
            "issued_at": "2026-01-01T00:00:00Z",
            "ok": False,
            "request_id": request_id,
            "error": error,
        }
    ).encode("utf-8")


def test_happy_path_returns_decoded_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    audio = b"RIFF\x00\x00\x00\x00WAVEfake-wav-bytes"
    fake_nc = _FakeNc(reply_payload=_tts_response_ok(audio))

    async def fake_connect(url: str, **kwargs):
        assert url == "nats://example:4222"
        assert kwargs.get("inbox_prefix") == "_inbox.voice-client"
        return fake_nc

    monkeypatch.setenv("NATS_URL", "nats://example:4222")
    monkeypatch.setattr(synthesize_client, "nats_connect", fake_connect)

    result = asyncio.run(synthesize_client.synthesize_via_nats("Bonjour", engine="qwen-fast"))

    assert result["audio"] == audio
    assert result["mime_type"] == "audio/wav"
    assert result["duration_ms"] == 1234
    assert fake_nc.drained and fake_nc.closed
    subject, payload, timeout = fake_nc.requests[0]
    assert subject == "lyra.voice.tts.request"
    assert timeout == 60.0
    sent = json.loads(payload)
    assert sent["text"] == "Bonjour"
    assert sent["engine"] == "qwen-fast"


def test_error_response_from_satellite_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_nc = _FakeNc(reply_payload=_tts_response_err("engine OOM"))

    async def fake_connect(url: str, **kwargs):
        return fake_nc

    monkeypatch.setenv("NATS_URL", "nats://example:4222")
    monkeypatch.setattr(synthesize_client, "nats_connect", fake_connect)

    result = asyncio.run(synthesize_client.synthesize_via_nats("Bonjour"))

    assert result == {"error": "engine OOM"}
    assert fake_nc.drained and fake_nc.closed


def test_timeout_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_nc = _FakeNc(raise_on_request=asyncio.TimeoutError())

    async def fake_connect(url: str, **kwargs):
        return fake_nc

    monkeypatch.setenv("NATS_URL", "nats://example:4222")
    monkeypatch.setattr(synthesize_client, "nats_connect", fake_connect)

    result = asyncio.run(synthesize_client.synthesize_via_nats("Bonjour", timeout=5.0))

    assert "timed out" in result["error"]
    assert "5.0" in result["error"]
    assert fake_nc.drained and fake_nc.closed


def test_missing_nats_url_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NATS_URL", raising=False)

    result = asyncio.run(synthesize_client.synthesize_via_nats("Bonjour"))

    assert result == {"error": "NATS_URL environment variable not set"}


def test_connect_failure_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_connect(url: str, **kwargs):
        raise ConnectionRefusedError("nats unreachable")

    monkeypatch.setenv("NATS_URL", "nats://example:4222")
    monkeypatch.setattr(synthesize_client, "nats_connect", fake_connect)

    result = asyncio.run(synthesize_client.synthesize_via_nats("Bonjour"))

    assert "Cannot connect to NATS" in result["error"]
    assert "nats unreachable" in result["error"]
