"""SttNatsAdapter — voicecli NATS satellite for STT transcription requests."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.voice import SUBJECTS as VOICE_SUBJECTS
from roxabi_contracts.voice.models import SttResponse
from roxabi_nats import NatsAdapterBase
from voicecli.adapters.nats._transcribe_runner import SttRunnerState, run_transcription
from voicecli.adapters.nats._validation import validate_stt_request
from voicecli.adapters.nats.queue_groups import STT_WORKERS
from voicecli.adapters.nats.requests import SttRequest
from voicecli.adapters.nats.tempdir import TEMP_ROOT, cleanup

# voicecli.api is NOT imported at module level — deferred to keep startup fast
# and avoid pulling torch/faster-whisper when only inspecting the adapter (e.g. --help).

log = logging.getLogger(__name__)


SUBJECT = VOICE_SUBJECTS.stt_request
HEARTBEAT_SUBJECT = VOICE_SUBJECTS.stt_heartbeat

# Audio shape helpers + size cap are re-exported here so tests + adapter callers
# keep importing from voicecli.adapters.nats.transcribe_adapter. The actual definitions live in
# _audio_utils.py to keep the adapter ↔ runner dependency direction one-way
# (the runner imports the helpers from _audio_utils directly, not from here).
from voicecli.adapters.nats._audio_utils import (  # noqa: E402
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
    "SttRequest",
    "SttNatsAdapter",
    "SUBJECT",
    "HEARTBEAT_SUBJECT",
]


def _err_stt(trace_id: str, request_id: str, error: str, job_id: str | None = None) -> bytes:
    fields: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "trace_id": trace_id,
        "issued_at": datetime.now(timezone.utc),
        "ok": False,
        "request_id": request_id or "",
        "error": error,
    }
    # job_id=None must NOT be set — _validate_job_id raises TypeError on None.
    # Let default_factory shim fire when job_id is absent.
    if job_id:
        fields["job_id"] = job_id
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
            wait_ready=False,  # worker semantics — see NatsAdapterBase docstring
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
        job_id: str | None = payload.get("job_id") or None
        if not request_id:
            await self.reply(msg, _err_stt(trace_id, "", "malformed_request", job_id))
            return

        outcome = validate_stt_request(payload)
        if outcome.error_code is not None:
            await self.reply(msg, _err_stt(trace_id, request_id, outcome.error_code, job_id))
            return
        req = outcome.request

        if self.reject_when_full:
            # Non-blocking acquire: avoid the race in _sem.locked()
            try:
                await asyncio.wait_for(self._sem.acquire(), timeout=0)
            except asyncio.TimeoutError:
                await self.reply(
                    msg, _err_stt(trace_id, req.request_id, "capacity_exceeded", job_id)
                )
                return
            try:
                await self._run_transcription(
                    msg,
                    req.request_id,
                    req.blob_ref,
                    req.to_overrides(),
                    trace_id=trace_id,
                    job_id=job_id,
                )
            finally:
                self._sem.release()
        else:
            async with self._sem:
                await self._run_transcription(
                    msg,
                    req.request_id,
                    req.blob_ref,
                    req.to_overrides(),
                    trace_id=trace_id,
                    job_id=job_id,
                )

    async def _run_transcription(
        self,
        msg: Any,
        request_id: str,
        blob_ref: Any,
        overrides: dict,
        *,
        trace_id: str,
        job_id: str | None = None,
    ) -> None:
        out_path = None
        try:
            ok, result, out_path = await run_transcription(
                self._runner_state,
                self.default_model,
                blob_ref,
                request_id,
                TEMP_ROOT,
                overrides,
                trace_id=trace_id,
            )
            if not ok:
                await self.reply(msg, _err_stt(trace_id, request_id, result, job_id))  # type: ignore[arg-type]
                return
            fields = result  # type: ignore[assignment]
            # job_id=None must NOT be passed explicitly — let default_factory shim fire instead.
            job_id_kwarg: dict[str, str] = {}
            if job_id:
                job_id_kwarg["job_id"] = job_id
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
                    **job_id_kwarg,
                )
                .model_dump_json(exclude_none=True)
                .encode(),
            )
        finally:
            if out_path is not None:
                cleanup(out_path)
