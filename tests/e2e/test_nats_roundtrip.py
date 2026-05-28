"""E2E round-trip tests for STT + TTS NATS satellites.

Three test tiers:

1. ``test_nats_roundtrip`` — docker-compose stack (session-scoped fixtures);
   skips on machines without Docker. Tests the full satellite pipeline.

2. ``test_stt_roundtrip_via_fake_blobstore`` — in-process; no Docker, no NATS.
   Exercises the STT adapter + runner + BlobStore put→get hop using a shared
   FakeBlobStore injected via monkeypatch. Validates V2 contract: the adapter
   accepts a ``blob_ref`` payload, runner fetches bytes, returns ``text``.

3. ``test_tts_roundtrip_via_fake_blobstore`` — in-process; no Docker, no NATS.
   Exercises the TTS adapter + runner + BlobStore put hop. Engine writes WAV,
   runner PUTs to FakeBlobStore, response carries ``blob_ref``. Validates V2
   egress contract symmetrically with the STT case.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.e2e.stub_hub import FakeBlobStore
from _fakes import _BLOBSTORE_PATCH_PATH, FakeMsg, FakeNatsConn


# ---------------------------------------------------------------------------
# Docker-compose E2E (skipped pending lyra#1067 atomic landing)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Deferred until lyra#1067 (V5 lyra-side worker wiring) lands atomically. "
        "Post-V2, the satellites call HttpBlobStore.put/get which needs a real "
        "BlobStore service in the compose stack (not present today — only nats + "
        "voicecli-mock satellites are spun up). stub_hub.run_once also still uses "
        "the V1 audio_b64 payload shape. Both are tracked as part of the lyra-side "
        "atomic merge. In-process V2 coverage is provided by "
        "test_stt_roundtrip_via_fake_blobstore + test_tts_roundtrip_via_fake_blobstore."
    ),
)
def test_nats_roundtrip(nkey_seed: tuple[Path, str], compose_stack: dict) -> None:
    """Full contract proof: hub ↔ TTS satellite, hub ↔ STT satellite."""
    from tests.e2e.stub_hub import run_once

    seed_path, _ = nkey_seed
    run_once(seed_path=seed_path, nats_url="nats://localhost:4222")


# ---------------------------------------------------------------------------
# In-process STT E2E via shared FakeBlobStore (no Docker, no NATS)
# ---------------------------------------------------------------------------


def test_stt_roundtrip_via_fake_blobstore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """STT adapter + runner V2 roundtrip: put→get via shared FakeBlobStore.

    Validates V2 BlobStore plumbing: the adapter accepts a ``blob_ref`` payload,
    the runner fetches bytes via ``get_blobstore().get``, and ``api.transcribe``
    runs against the fetched scoped_path. Both ends share the same
    ``FakeBlobStore`` instance so the put→get hop is genuine.

    ``api.transcribe`` is mocked so the test runs without ``faster_whisper``
    installed — the point is the BlobStore plumbing, not the Whisper model.
    """
    # Arrange — shared FakeBlobStore
    fake_store = FakeBlobStore()

    monkeypatch.setattr(
        _BLOBSTORE_PATCH_PATH,
        lambda: fake_store,
    )

    # Mock api.transcribe so the test doesn't need faster_whisper installed.
    # The runner imports `from voicecli import api` and calls api.transcribe(...)
    # synchronously inside an executor. We capture what bytes the runner wrote
    # (proving the put→get hop worked) and return a deterministic result.
    captured_paths: list[Path] = []

    def _fake_transcribe(audio_path, *args, **kwargs):
        captured_paths.append(Path(audio_path))
        from voicecli.runtime.transcribe import TranscriptionResult

        return TranscriptionResult(
            text="hello from fake",
            language="en",
            segments=[],
        )

    monkeypatch.setattr("voicecli.api.transcribe", _fake_transcribe)

    # Build a minimal silent WAV payload and PUT it into FakeBlobStore
    # (simulating what transcribe_client.transcribe_via_nats would do).
    import struct

    sample_rate = 22050
    num_channels = 1
    bits_per_sample = 16
    num_samples = sample_rate
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
        16,
        1,
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    wav_bytes = header + bytes(data_size)

    blob_ref_obj = asyncio.run(fake_store.put(wav_bytes, mime="audio/wav", source="voicecli-test"))

    # Build the SttRequest payload (V2 — blob_ref dict, not audio_b64)
    now_iso = datetime.now(timezone.utc).isoformat()
    request_id = uuid.uuid4().hex
    payload = {
        "contract_version": "1",
        "request_id": request_id,
        "trace_id": "e2e-trace-001",
        "blob_ref": {
            "store_key": blob_ref_obj.store_key,
            "mime": blob_ref_obj.mime,
            "size": blob_ref_obj.size,
            "source": blob_ref_obj.source,
            "content_hash": blob_ref_obj.content_hash,
            "created_at": now_iso,
        },
    }

    # Set up the real SttNatsAdapter (model_warm=True to skip GPU warmup)
    from voicecli.adapters.nats.config import DEFAULT_MODEL
    from voicecli.adapters.nats.transcribe_adapter import SttNatsAdapter

    adapter = SttNatsAdapter(default_model=DEFAULT_MODEL, max_concurrent=1)
    adapter._model_warm = True
    msg = FakeMsg()
    adapter._nc = FakeNatsConn(msg)

    # Redirect TEMP_ROOT so the runner writes into tmp_path (not /tmp/voicecli-nats)
    with patch("voicecli.adapters.nats.transcribe_adapter.TEMP_ROOT", tmp_path):
        asyncio.run(adapter.handle(msg, payload))

    # Assert
    reply = msg.last_reply()
    assert reply["ok"] is True, f"STT roundtrip failed: {reply}"
    assert reply["request_id"] == request_id
    assert "text" in reply and reply["text"] is not None

    # Verify the BlobStore.get hop actually happened
    assert blob_ref_obj.store_key in fake_store.get_calls, (
        "runner did not call BlobStore.get — blob_ref hop was skipped"
    )


# ---------------------------------------------------------------------------
# In-process TTS E2E via shared FakeBlobStore (no Docker, no NATS) — T18
# ---------------------------------------------------------------------------


def test_tts_roundtrip_via_fake_blobstore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TTS adapter + runner V2 roundtrip: synthesize → put → blob_ref.

    Symmetric with the STT case: ``api.generate`` is mocked to write a stub
    WAV; runner PUTs bytes to FakeBlobStore; adapter builds TtsResponse with
    ``blob_ref``. No bytes-fetch hop on the TTS side — the consumer (lyra
    subscriber) does the get later.
    """
    # Arrange — shared FakeBlobStore
    fake_store = FakeBlobStore()
    monkeypatch.setattr(
        _BLOBSTORE_PATCH_PATH,
        lambda: fake_store,
    )

    # Build a minimal but valid silent WAV (runner reads it via wave.open for duration).
    import io
    import wave

    _wav_buf = io.BytesIO()
    with wave.open(_wav_buf, "wb") as _wf:
        _wf.setnchannels(1)
        _wf.setsampwidth(2)
        _wf.setframerate(22050)
        _wf.writeframes(b"\x00\x00" * 100)
    minimal_wav = _wav_buf.getvalue()

    # Register a mock engine in the registry so adapter's _engine_available check passes.
    # The adapter imports _get_registry fresh from voicecli.engines.engine on each check,
    # so we patch the module attribute (mirrors test_tts_adapter.py convention).
    class _FakeEngine:
        name = "mock"

        def generate(self, text: str, voice, output_path: Path, **kwargs) -> Path:
            Path(output_path).write_bytes(minimal_wav)
            return output_path

    monkeypatch.setattr(
        "voicecli.engines.engine._get_registry",
        lambda: {"mock": _FakeEngine},
    )

    # Mock api.generate (runner-level) to write the same minimal WAV.
    def _fake_generate(text: str, *, engine: str, output: Path, **kwargs):
        Path(output).write_bytes(minimal_wav)

    monkeypatch.setattr("voicecli.api.generate", _fake_generate)

    # Build TtsRequest payload (V2 — text-only request, blob_ref comes back in response)
    request_id = uuid.uuid4().hex
    payload = {
        "contract_version": "1",
        "request_id": request_id,
        "trace_id": "e2e-tts-trace-001",
        "text": "hello world",
        "engine": "mock",
    }

    from voicecli.adapters.nats.synthesize_adapter import TtsNatsAdapter

    adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
    msg = FakeMsg()
    adapter._nc = FakeNatsConn(msg)

    asyncio.run(adapter.handle(msg, payload))

    # Assert response carries blob_ref (not audio_b64)
    reply = msg.last_reply()
    assert reply["ok"] is True, f"TTS roundtrip failed: {reply}"
    assert reply["request_id"] == request_id
    assert "audio_b64" not in reply, "V2 must not carry inline audio bytes"
    assert "blob_ref" in reply
    assert reply["blob_ref"]["store_key"].startswith("sha256:")
    assert reply["blob_ref"]["mime"] == "audio/wav"

    # Verify BlobStore.put was called with the synthesized WAV bytes
    assert len(fake_store.put_calls) == 1
    put_call = fake_store.put_calls[0]
    assert put_call["mime"] == "audio/wav"
    assert put_call["source"] == "voicecli"
    # FakeBlobStore stores bytes under store_key in _blobs; assert hop content
    stored = fake_store._blobs[put_call["store_key"]]
    assert stored == minimal_wav
