"""OTel lifecycle hook tests for voiceCLI NATS adapters (#2069 Block 6)."""

from __future__ import annotations

import pytest
from roxabi_otel import InMemorySpanRecorder

from roxabi_contracts.telemetry import ATTR_BLOB_REF_IN, ATTR_BLOB_REF_OUT, ATTR_MODEL
from voicecli.adapters.nats.synthesize_adapter import TtsNatsAdapter
from voicecli.adapters.nats.transcribe_adapter import SttNatsAdapter

_TRACE = "550e8400-e29b-41d4-a716-446655440000"
_JOB = "a" * 32


class TestVoiceTelemetryHooks:
    @pytest.mark.asyncio
    async def test_stt_adapter_records_blob_in_and_model(self) -> None:
        recorder = InMemorySpanRecorder()
        adapter = SttNatsAdapter(
            default_model="whisper-large",
            lifecycle_hooks=recorder.hooks("voicecli-stt"),
        )
        msg = type("M", (), {"subject": "factory.voice.stt.request"})()
        payload = {
            "trace_id": _TRACE,
            "job_id": _JOB,
            "request_id": "req-1",
            "blob_ref": {"store_key": "audio/in/key"},
            "model": "whisper-large",
        }

        async def _noop_handle(_msg: object, _payload: dict) -> None:
            return None

        adapter.handle = _noop_handle  # type: ignore[method-assign]
        await adapter._invoke_handle_with_hooks(msg, payload)

        spans = recorder.finished_spans()
        assert len(spans) == 1
        assert spans[0].attributes[ATTR_BLOB_REF_IN] == "audio/in/key"
        assert spans[0].attributes[ATTR_MODEL] == "whisper-large"
        assert "text" not in spans[0].attributes
        assert "transcript" not in spans[0].attributes

    @pytest.mark.asyncio
    async def test_tts_adapter_records_blob_out_and_model(self) -> None:
        recorder = InMemorySpanRecorder()
        adapter = TtsNatsAdapter(
            default_engine="qwen3-tts",
            lifecycle_hooks=recorder.hooks("voicecli-tts"),
        )
        adapter._otel_work_attrs[_JOB] = {ATTR_BLOB_REF_OUT: "audio/out/key"}
        msg = type("M", (), {"subject": "factory.voice.tts.request"})()
        payload = {
            "trace_id": _TRACE,
            "job_id": _JOB,
            "request_id": "req-2",
            "engine": "qwen3-tts",
            "text": "hello",
        }

        async def _noop_handle(_msg: object, _payload: dict) -> None:
            return None

        adapter.handle = _noop_handle  # type: ignore[method-assign]
        await adapter._invoke_handle_with_hooks(msg, payload)

        spans = recorder.finished_spans()
        assert len(spans) == 1
        assert spans[0].attributes[ATTR_BLOB_REF_OUT] == "audio/out/key"
        assert spans[0].attributes[ATTR_MODEL] == "qwen3-tts"
        assert "text" not in spans[0].attributes
