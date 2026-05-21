"""SttNatsAdapter — voicecli NATS satellite for STT transcription requests."""

from __future__ import annotations

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.voice import SUBJECTS as VOICE_SUBJECTS
from roxabi_contracts.voice.models import SttResponse
from roxabi_nats import NatsAdapterBase
from voicecli.nats._stt_runner import SttRunnerState, run_transcription
from voicecli.nats._validation import validate_stt_request
from voicecli.nats.queue_groups import STT_WORKERS
from voicecli.nats.tempdir import cleanup, scoped_path

# voicecli.api is NOT imported at module level — deferred to keep startup fast
# and avoid pulling torch/faster-whisper when only inspecting the adapter (e.g. --help).

log = logging.getLogger(__name__)

SUBJECT = VOICE_SUBJECTS.stt_request
HEARTBEAT_SUBJECT = VOICE_SUBJECTS.stt_heartbeat

# Audio shape helpers + size cap are re-exported here so tests + adapter callers
# keep importing from voicecli.nats.stt_adapter. The actual definitions live in
# _audio_utils.py to keep the adapter ↔ runner dependency direction one-way
# (the runner imports the helpers from _audio_utils directly, not from here).
from voicecli.nats._audio_utils import (  # noqa: E402
    MAX_AUDIO_B64_LEN,
    _MIME_TO_EXT,
    _duration_from_segments,
    _ext_from_mime,
)

__all__ = [
    "MAX_AUDIO_B64_LEN",
    "_MIME_TO_EXT",
    "_duration_from_segments",
    "_ext_from_mime",
    "SttNatsAdapter",
    "SUBJECT",
    "HEARTBEAT_SUBJECT",
]


def _err_stt(trace_id: str, request_id: str, error: str) -> bytes:
    fields: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "trace_id": trace_id,
        "issued_at": datetime.now(timezone.utc),
        "ok": False,
        "request_id": request_id or "",
        "error": error,
    }
    # Skip validation only when request_id is empty (otherwise the contract requires it).
    m = SttResponse.model_construct(**fields) if not request_id else SttResponse(**fields)
    return m.model_dump_json(exclude_none=True).encode()


class SttNatsAdapter(NatsAdapterBase):
    def __init__(
        self,
        *,
        default_model: str,
        max_concurrent: int = 2,
        reject_when_full: bool = False,
        heartbeat_interval: float = 5.0,
        drain_timeout: float = 30.0,
    ) -> None:
        super().__init__(
            SUBJECT,
            STT_WORKERS,
            "stt",
            1,
            heartbeat_subject=HEARTBEAT_SUBJECT,
            heartbeat_interval=heartbeat_interval,
            drain_timeout=drain_timeout,
            inbox_prefix="_inbox.voice-stt",
        )
        self.default_model = default_model
        self.max_concurrent = max_concurrent
        self.reject_when_full = reject_when_full
        self.model_loaded: str | None = None
        self._sem = asyncio.Semaphore(max_concurrent)
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent)
        self._runner_state = SttRunnerState(
            executor=self._executor,
            set_model_warm=self._set_model_warm,
            set_model_loaded=self._set_model_loaded,
            model_warm=False,
        )

    @property
    def _model_warm(self) -> bool:
        return self._runner_state.model_warm

    @_model_warm.setter
    def _model_warm(self, v: bool) -> None:
        self._runner_state.model_warm = v

    def _set_model_warm(self, v: bool) -> None:
        self._runner_state.model_warm = v

    def _set_model_loaded(self, v: str | None) -> None:
        self.model_loaded = v

    def heartbeat_payload(self) -> dict:
        payload = super().heartbeat_payload()
        payload["model_loaded"] = self.model_loaded
        payload["active_requests"] = self.max_concurrent - self._sem._value
        return payload

    def _extra_subjects(self) -> list[str]:
        return [f"{self.subject}.{self._worker_id}"]

    async def handle(self, msg: Any, payload: dict) -> None:  # type: ignore[override]
        trace_id = payload.get("trace_id") or "unknown"
        request_id = payload.get("request_id", "")
        if not request_id:
            await self.reply(msg, _err_stt(trace_id, "", "malformed_request"))
            return

        # Reject path-traversal or oversized request IDs at ingestion
        if not re.match(r"^[A-Za-z0-9_-]{1,128}$", request_id):
            await self.reply(
                msg,
                _err_stt(trace_id, request_id[:64] if request_id else "", "malformed_request"),
            )
            return

        outcome = validate_stt_request(payload)
        if outcome.error_code is not None:
            await self.reply(msg, _err_stt(trace_id, request_id, outcome.error_code))
            return
        audio_b64 = payload["audio_b64"]  # validated by validate_stt_request to be str
        overrides = outcome.overrides or {}

        if self.reject_when_full:
            # Non-blocking acquire: avoid the race in _sem.locked()
            try:
                await asyncio.wait_for(self._sem.acquire(), timeout=0)
            except asyncio.TimeoutError:
                await self.reply(msg, _err_stt(trace_id, request_id, "capacity_exceeded"))
                return
            try:
                await self._run_transcription(
                    msg, payload, request_id, audio_b64, overrides, trace_id=trace_id
                )
            finally:
                self._sem.release()
        else:
            async with self._sem:
                await self._run_transcription(
                    msg, payload, request_id, audio_b64, overrides, trace_id=trace_id
                )

    async def _run_transcription(
        self,
        msg: Any,
        payload: dict,
        request_id: str,
        audio_b64: str,
        overrides: dict,
        *,
        trace_id: str,
    ) -> None:
        ext = _ext_from_mime(payload.get("mime_type"))
        out_path = scoped_path(request_id, ext)
        try:
            ok, result = await run_transcription(
                self._runner_state,
                self.default_model,
                MAX_AUDIO_B64_LEN,
                payload,
                request_id,
                audio_b64,
                overrides,
                trace_id=trace_id,
            )
            if not ok:
                await self.reply(msg, _err_stt(trace_id, request_id, result))  # type: ignore[arg-type]
                return
            fields = result  # type: ignore[assignment]
            await self.reply(
                msg,
                SttResponse(
                    contract_version=CONTRACT_VERSION,
                    trace_id=trace_id,
                    issued_at=datetime.now(timezone.utc),
                    ok=True,
                    request_id=request_id,
                    text=fields["text"],
                    language=fields["language"],
                    duration_seconds=fields["duration_seconds"],
                )
                .model_dump_json(exclude_none=True)
                .encode(),
            )
        finally:
            cleanup(out_path)
