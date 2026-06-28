"""Tests for voice NATS lifecycle list/status (ADR-095)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.voice import SUBJECTS, VoiceLifecycleRequest

try:
    from voicecli.adapters.nats.synthesize_adapter import TtsNatsAdapter
    from voicecli.adapters.nats.transcribe_adapter import SttNatsAdapter

    _IMPORT_ERROR = None
except ImportError as exc:
    _IMPORT_ERROR = exc
    TtsNatsAdapter = None  # type: ignore[assignment,misc]
    SttNatsAdapter = None  # type: ignore[assignment,misc]


def _require() -> None:
    if _IMPORT_ERROR is not None:
        pytest.fail(str(_IMPORT_ERROR))


class _Msg:
    def __init__(self, subject: str) -> None:
        self.subject = subject
        self.reply = "_inbox.test"
        self.data = b""
        self.response: bytes | None = None

    async def respond(self, data: bytes) -> None:
        self.response = data


class _Nc:
    def __init__(self, msg: _Msg) -> None:
        self._msg = msg
        self.published: list[tuple[str, bytes]] = []

    async def publish(self, subject: str, data: bytes) -> None:
        self.published.append((subject, data))
        if subject == self._msg.reply:
            self._msg.response = data


def _lifecycle_payload(*, op: str) -> dict:
    return VoiceLifecycleRequest(
        contract_version=CONTRACT_VERSION,
        trace_id="trace-1",
        issued_at=datetime.now(timezone.utc),
        request_id="req-lifecycle-1",
        op=op,  # type: ignore[arg-type]
    ).model_dump()


def test_tts_lifecycle_list_replies_with_engines() -> None:
    _require()
    adapter = TtsNatsAdapter(default_engine="mock")
    msg = _Msg(SUBJECTS.tts_lifecycle_list)
    adapter._nc = _Nc(msg)  # noqa: SLF001
    asyncio.run(adapter.handle_lifecycle(msg, _lifecycle_payload(op="list")))
    assert msg.response is not None
    body = json.loads(msg.response)
    assert body["ok"] is True
    assert "engines" in body["data"]
    assert "samples" in body["data"]
    assert body["data"]["max_cached_engines"] == 1


def test_stt_lifecycle_list_replies_with_models() -> None:
    _require()
    adapter = SttNatsAdapter(default_model="large-v3-turbo")
    msg = _Msg(SUBJECTS.stt_lifecycle_list)
    adapter._nc = _Nc(msg)  # noqa: SLF001
    asyncio.run(adapter.handle_lifecycle(msg, _lifecycle_payload(op="list")))
    assert msg.response is not None
    body = json.loads(msg.response)
    assert body["ok"] is True
    assert any(m["name"] == "large-v3-turbo" for m in body["data"]["models"])
