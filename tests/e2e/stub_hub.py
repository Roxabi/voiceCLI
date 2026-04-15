"""Stub hub: publishes one TTS + one STT request, awaits replies, asserts ADR-044 schema."""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import uuid
from pathlib import Path
from typing import Any

import nats
from nats.errors import NoRespondersError

TTS_REQUEST_SUBJECT = "lyra.voice.tts.request"
STT_REQUEST_SUBJECT = "lyra.voice.stt.request"
REPLY_TIMEOUT = 10.0
# Cold-start budget for satellite containers to `uv sync` and subscribe.
# CI (cold docker pulls + fresh uv cache) needs ~150–250s; local warm runs ~60s.
SUBSCRIBER_WAIT = 300.0
SUBSCRIBER_POLL_INTERVAL = 1.0


def _silent_wav_bytes() -> bytes:
    """Build a 1-second silent WAV (22050 Hz, 16-bit, mono) using struct.pack.

    Ported from src/voicecli/engines/mock.py — same byte-level construction.
    """
    sample_rate = 22050
    num_channels = 1
    bits_per_sample = 16
    num_samples = sample_rate  # 1 second
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = num_samples * block_align
    chunk_size = 36 + data_size

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        chunk_size,
        b"WAVE",
        b"fmt ",
        16,  # PCM subchunk size
        1,  # PCM format
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + bytes(data_size)


_SILENT_WAV_B64 = base64.b64encode(_silent_wav_bytes()).decode("ascii")


async def _round_trip(nc: Any, subject: str, payload: dict) -> dict:
    """Publish payload to subject and return the decoded JSON reply.

    Retries on NoRespondersError up to SUBSCRIBER_WAIT seconds — covers the
    window where NATS is up but the satellite container is still running
    `uv sync` and hasn't subscribed yet (cold-start, ~30-60s).
    """
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    deadline = asyncio.get_running_loop().time() + SUBSCRIBER_WAIT
    while True:
        try:
            msg = await nc.request(subject, raw, timeout=REPLY_TIMEOUT)
            return json.loads(msg.data)
        except NoRespondersError:
            if asyncio.get_running_loop().time() >= deadline:
                raise
            await asyncio.sleep(SUBSCRIBER_POLL_INTERVAL)


def _assert_reply(reply: dict, request_id: str, payload_key: str) -> None:
    """Assert ADR-044 reply envelope fields.

    Checks:
    - contract_version == "1"
    - request_id echoed back
    - ok is True
    - payload_key present and non-None
    - If payload_key == "audio_b64": decoded bytes start with b"RIFF"
    """
    assert reply.get("contract_version") == "1", f"contract_version mismatch. full reply: {reply}"
    assert reply.get("request_id") == request_id, (
        f"request_id echo mismatch. expected={request_id!r}. full reply: {reply}"
    )
    assert reply.get("ok") is True, f"ok is not True. full reply: {reply}"
    assert payload_key in reply and reply[payload_key] is not None, (
        f"payload_key {payload_key!r} missing or None. full reply: {reply}"
    )
    if payload_key == "audio_b64":
        decoded = base64.b64decode(reply["audio_b64"])
        assert decoded[:4] == b"RIFF", (
            f"audio_b64 does not decode to a WAV (missing RIFF header). full reply: {reply}"
        )


async def _run(seed_path: Path, nats_url: str) -> None:
    """Connect to NATS, send one TTS + one STT request, assert both replies."""
    nc = await nats.connect(
        servers=nats_url,
        nkeys_seed=str(seed_path),
        connect_timeout=10.0,
    )
    try:
        # --- TTS round-trip ---
        tts_request_id = uuid.uuid4().hex
        tts_payload = {
            "contract_version": "1",
            "request_id": tts_request_id,
            "text": "hello",
            "engine": "mock",
        }
        tts_reply = await _round_trip(nc, TTS_REQUEST_SUBJECT, tts_payload)
        _assert_reply(tts_reply, tts_request_id, "audio_b64")

        # --- STT round-trip ---
        stt_request_id = uuid.uuid4().hex
        stt_payload = {
            "contract_version": "1",
            "request_id": stt_request_id,
            "audio_b64": _SILENT_WAV_B64,
            "mime_type": "audio/wav",
            "model": "mock",
        }
        stt_reply = await _round_trip(nc, STT_REQUEST_SUBJECT, stt_payload)
        _assert_reply(stt_reply, stt_request_id, "text")
    finally:
        await nc.drain()


def run_once(seed_path: Path, nats_url: str) -> None:
    """Entry point: run the TTS + STT round-trip synchronously."""
    asyncio.run(_run(seed_path, nats_url))
