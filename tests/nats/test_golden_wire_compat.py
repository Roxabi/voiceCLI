"""Golden-file regression suite: byte-equal wire compat vs staging baseline.

Every *.json file in tests/nats/golden/ is an independent test case.
Adding a new golden file automatically adds a new parametrized test —
no changes to this file needed.

HOW TO REGENERATE GOLDENS
--------------------------
    git worktree add /tmp/voicecli-staging-golden origin/staging
    cp tools/capture_golden.py /tmp/voicecli-staging-golden/tools/
    cd /tmp/voicecli-staging-golden && uv run --extra nats python tools/capture_golden.py
    git worktree remove --force /tmp/voicecli-staging-golden
Then commit the updated tests/nats/golden/*.json files.

V2 UPDATE (#144)
-----------------
STT corpus now uses ``blob_ref`` dicts (not ``audio_b64``) per V2 contract.
Error-path golden fixtures are unchanged — error responses don't carry audio.
stt_request_v2.json and tts_response_v2.json document the V2 wire shape;
they are NOT replay cases (no matching corpus entry) and are excluded from
the parametrized test via the _golden_params exclusion list.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
from pathlib import Path
from unittest.mock import patch

import pytest

GOLDEN_DIR = Path(__file__).parent / "golden"

# ---------------------------------------------------------------------------
# V2 blob_ref dict for STT corpus payloads
# ---------------------------------------------------------------------------

_BLOB_REF_V2 = {
    "store_key": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "content_hash": "deadbeef",
    "mime": "audio/wav",
    "size": 16,
    "source": "voicecli",
}

# Maps case_name → payload dict (exactly as used during capture).
# V2: STT payloads use blob_ref (not audio_b64).
_CORPUS: dict[str, dict] = {
    # TTS cases
    "tts_missing_request_id": {
        "text": "Hello",
        "engine": "mock",
        "trace_id": "trace-tts-1",
    },
    "tts_malformed_request_id": {
        "request_id": "../escape",
        "text": "Hello",
        "engine": "mock",
        "trace_id": "trace-tts-2",
    },
    "tts_missing_text": {
        "request_id": "req-notext",
        "engine": "mock",
        "trace_id": "trace-tts-3",
    },
    "tts_non_string_text": {
        "request_id": "req-badtext",
        "text": 42,
        "engine": "mock",
        "trace_id": "trace-tts-4",
    },
    "tts_text_empty_after_strip": {
        "request_id": "req-allnl",
        "text": "\n\n\r\n\r",
        "engine": "mock",
        "trace_id": "trace-tts-5",
    },
    "tts_malformed_engine_space": {
        "request_id": "req-engsp",
        "text": "Hello",
        "engine": "a b",
        "trace_id": "trace-tts-6",
    },
    "tts_malformed_engine_wildcard": {
        "request_id": "req-engwild",
        "text": "Hello",
        "engine": "*.tts",
        "trace_id": "trace-tts-7",
    },
    "tts_engine_unavailable": {
        "request_id": "req-noeng",
        "text": "Hello",
        "engine": "ghost-engine",
        "trace_id": "trace-tts-8",
    },
    # STT cases — V2: blob_ref replaces audio payload for validation errors.
    # All these cases test validation errors where the response shape is:
    # {ok: false, error: "malformed_request"} — identical to V1 error responses.
    "stt_missing_request_id": {
        "blob_ref": _BLOB_REF_V2,
        "mime_type": "audio/wav",
        "trace_id": "trace-stt-1",
    },
    "stt_malformed_request_id": {
        "request_id": "../escape",
        "blob_ref": _BLOB_REF_V2,
        "mime_type": "audio/wav",
        "trace_id": "trace-stt-2",
    },
    "stt_missing_audio": {
        "request_id": "req-noaudio",
        "mime_type": "audio/wav",
        "trace_id": "trace-stt-3",
    },
    "stt_non_string_audio": {
        "request_id": "req-badaudio",
        "blob_ref": "not-a-dict",
        "mime_type": "audio/wav",
        "trace_id": "trace-stt-4",
    },
    "stt_invalid_task": {
        "request_id": "req-badtask",
        "blob_ref": _BLOB_REF_V2,
        "mime_type": "audio/wav",
        "task": "summarize",
        "trace_id": "trace-stt-5",
    },
    "stt_bool_detection_segments": {
        "request_id": "req-boolseg",
        "blob_ref": _BLOB_REF_V2,
        "mime_type": "audio/wav",
        "language_detection_segments": True,
        "trace_id": "trace-stt-6",
    },
    "stt_wrong_type_language": {
        "request_id": "req-badlang",
        "blob_ref": _BLOB_REF_V2,
        "mime_type": "audio/wav",
        "language": 42,
        "trace_id": "trace-stt-7",
    },
}

# Golden files that document the V2 wire shape but are NOT replay cases.
# Excluded from _golden_params so they don't require corpus entries.
_SCHEMA_DOCS = frozenset({"stt_request_v2", "tts_response_v2"})

# ---------------------------------------------------------------------------
# Minimal fakes (no external test helpers imported — standalone)
# ---------------------------------------------------------------------------


class _FakeMsg:
    def __init__(self) -> None:
        self.data = b""
        self.reply = "_INBOX.golden-replay"
        self._published: list[bytes] = []

    async def respond(self, data: bytes) -> None:
        self._published.append(data)


class _FakeNatsConn:
    def __init__(self, msg: _FakeMsg) -> None:
        self._msg = msg
        self.is_connected = True
        self.is_closed = False

    async def publish(self, _subject: str, data: bytes) -> None:
        del _subject
        await self._msg.respond(data)


class _SyncExecutor:
    def submit(self, fn, *args, **kwargs):
        f: concurrent.futures.Future = concurrent.futures.Future()
        try:
            f.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001
            f.set_exception(exc)
        return f


def _setup_adapter(adapter, msg: _FakeMsg) -> None:
    adapter._nc = _FakeNatsConn(msg)


# ---------------------------------------------------------------------------
# Parametrized test
# ---------------------------------------------------------------------------


def _golden_params() -> list[pytest.MarkDecorator]:
    """Collect all golden files as test parameters, excluding schema-doc fixtures."""
    params = []
    for path in sorted(GOLDEN_DIR.glob("*.json")):
        case_name = path.stem
        if case_name in _SCHEMA_DOCS:
            continue  # schema-documentation fixtures are not replay cases
        params.append(pytest.param(path, case_name, id=case_name))
    return params  # type: ignore[return-value]


@pytest.mark.parametrize("golden_path,case_name", _golden_params())
def test_golden_wire_compat(golden_path: Path, case_name: str) -> None:
    """Replay a fixed payload through this branch's adapter and compare byte-for-byte
    against the staging golden. Any field-content diff = regression.

    issued_at is excluded from both sides (timestamp varies per-run).
    Comparison is on the re-serialized JSON (sort_keys=True, indent=2) so
    JSON-formatting drift cannot mask content diffs.
    """
    assert case_name in _CORPUS, (
        f"Golden file {golden_path.name} has no matching corpus entry in _CORPUS. "
        f"Add the payload to _CORPUS or regenerate goldens."
    )

    payload = _CORPUS[case_name]
    golden_obj = json.loads(golden_path.read_text())

    # Run through this branch's adapter
    branch_raw = _run_adapter(case_name, payload)
    branch_obj = json.loads(branch_raw.decode())

    # Strip timestamp from both sides before comparing
    golden_obj.pop("issued_at", None)
    branch_obj.pop("issued_at", None)

    # Re-serialize both with canonical formatting for deterministic comparison
    golden_canonical = json.dumps(golden_obj, sort_keys=True, indent=2)
    branch_canonical = json.dumps(branch_obj, sort_keys=True, indent=2)

    assert branch_canonical == golden_canonical, (
        f"Wire response mismatch for {case_name}:\n"
        f"  staging: {golden_canonical}\n"
        f"  branch:  {branch_canonical}"
    )


# ---------------------------------------------------------------------------
# Adapter dispatch helpers
# ---------------------------------------------------------------------------


def _run_adapter(case_name: str, payload: dict) -> bytes:
    """Dispatch payload through the appropriate adapter, return raw reply bytes."""
    if case_name.startswith("tts_"):
        return asyncio.run(_run_tts(payload))
    elif case_name.startswith("stt_"):
        return asyncio.run(_run_stt(payload))
    else:
        raise ValueError(f"Unknown case prefix for: {case_name!r}")


async def _run_tts(payload: dict) -> bytes:
    from voicecli.adapters.nats.synthesize_adapter import TtsNatsAdapter

    adapter = TtsNatsAdapter(default_engine="mock", max_concurrent=1)
    adapter._executor = _SyncExecutor()  # type: ignore[assignment]
    msg = _FakeMsg()
    _setup_adapter(adapter, msg)

    with patch("voicecli.engines.engine._get_registry", return_value={"mock": object()}):
        await adapter.handle(msg, payload)

    assert msg._published, "No reply published by TTS adapter"
    return msg._published[-1]


async def _run_stt(payload: dict) -> bytes:
    from voicecli.adapters.nats.transcribe_adapter import SttNatsAdapter

    adapter = SttNatsAdapter(default_model="large-v3-turbo", max_concurrent=1)
    adapter._executor = _SyncExecutor()  # type: ignore[assignment]
    msg = _FakeMsg()
    _setup_adapter(adapter, msg)

    await adapter.handle(msg, payload)

    assert msg._published, "No reply published by STT adapter"
    return msg._published[-1]
