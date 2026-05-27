"""E2E round-trip tests for STT + TTS NATS satellites.

Two test tiers:

1. ``test_nats_roundtrip`` — docker-compose stack (session-scoped fixtures);
   skips on machines without Docker. Tests the full satellite pipeline.

2. ``test_stt_roundtrip_via_fake_blobstore`` — in-process; no Docker, no NATS.
   Exercises the STT adapter + runner + BlobStore put→get hop using a shared
   FakeBlobStore injected via monkeypatch. Validates V2 contract: the adapter
   accepts a ``blob_ref`` payload, runner fetches bytes, returns ``text``.
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


# ---------------------------------------------------------------------------
# Docker-compose E2E (unchanged — skips without Docker)
# ---------------------------------------------------------------------------


def test_nats_roundtrip(nkey_seed: tuple[Path, str], compose_stack: dict) -> None:
    """Full contract proof: hub ↔ TTS satellite, hub ↔ STT satellite."""
    from tests.e2e.stub_hub import run_once

    seed_path, _ = nkey_seed
    run_once(seed_path=seed_path, nats_url="nats://localhost:4222")


# ---------------------------------------------------------------------------
# In-process STT E2E via shared FakeBlobStore (no Docker, no NATS)
# ---------------------------------------------------------------------------


@pytest.mark.stt
def test_stt_roundtrip_via_fake_blobstore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """STT adapter + runner V2 roundtrip: put→get via shared FakeBlobStore.

    Simulates what transcribe_client.py does (put bytes → BlobRef) and what
    the STT adapter + runner do (get bytes from BlobRef → transcribe → text).

    Uses the same FakeBlobStore instance for both ends so put→get shares state,
    exactly as the spec requires for in-process E2E (T13).

    The VOICECLI_ENABLE_MOCK_ENGINE env var gates the mock Whisper engine that
    returns a deterministic result without loading a real model.
    """
    # Arrange — shared FakeBlobStore
    fake_store = FakeBlobStore()

    # Patch get_blobstore in both the blobs module (adapter + runner) and in
    # transcribe_client so they all share the same in-memory store.
    monkeypatch.setattr(
        "voicecli.adapters.nats.blobs.get_blobstore",
        lambda: fake_store,
    )

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
    # Import _fakes via its full path since tests/nats/ isn't on sys.path in e2e.
    import importlib.util
    import sys
    from pathlib import Path as _Path

    _fakes_path = _Path(__file__).parent.parent / "nats" / "_fakes.py"
    _spec = importlib.util.spec_from_file_location("_fakes_e2e", _fakes_path)
    _fakes_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
    _spec.loader.exec_module(_fakes_mod)  # type: ignore[union-attr]
    FakeMsg = _fakes_mod.FakeMsg
    FakeNatsConn = _fakes_mod.FakeNatsConn

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
