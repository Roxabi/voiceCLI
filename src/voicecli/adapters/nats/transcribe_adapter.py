"""SttNatsAdapter — voicecli NATS satellite for STT transcription requests."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.voice import SUBJECTS as VOICE_SUBJECTS
from roxabi_contracts.voice.models import SttRequest, SttResponse
from roxabi_nats import NatsAdapterBase
from roxabi_satellite.errors import VOICE_STT_RUNNER_ERRORS, resolve_worker_error
from roxabi_satellite.voice.replies import build_stt_error_reply, voice_validation_error
from voicecli.adapters.nats._lifecycle import LifecycleMixin
from voicecli.adapters.nats._stt_lifecycle import build_stt_list_data, build_stt_status_data
from voicecli.adapters.nats._transcribe_runner import SttRunnerState, run_transcription
from voicecli.adapters.nats._validation import validate_stt_request
from voicecli.adapters.nats.queue_groups import STT_WORKERS
from voicecli.adapters.nats.tempdir import TEMP_ROOT, cleanup

log = logging.getLogger(__name__)


SUBJECT = VOICE_SUBJECTS.stt_request
HEARTBEAT_SUBJECT = VOICE_SUBJECTS.stt_heartbeat

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


class SttNatsAdapter(LifecycleMixin, NatsAdapterBase):
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
            wait_ready=False,
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
        self.__init_lifecycle__()

    def _lifecycle_subjects(self) -> tuple[str, ...]:
        return (
            VOICE_SUBJECTS.stt_lifecycle_list,
            VOICE_SUBJECTS.stt_lifecycle_status,
        )

    async def _do_list(self, msg, req) -> None:
        await self._reply_ok(
            msg,
            req,
            data=build_stt_list_data(default_model=self.default_model),
        )

    async def _do_status(self, msg, req) -> None:
        await self._reply_ok(msg, req, data=build_stt_status_data(self))

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
        if msg.subject in self._lifecycle_subjects():
            await self.handle_lifecycle(msg, payload)
            return
        trace_id = payload.get("trace_id") or "unknown"
        request_id = payload.get("request_id", "")
        job_id: str | None = payload.get("job_id") or None
        if not request_id:
            await self.reply(
                msg,
                build_stt_error_reply(
                    trace_id, "", voice_validation_error("malformed_request"), job_id
                ),
            )
            return

        outcome = validate_stt_request(payload, default_model=self.default_model)
        if outcome.error_code is not None:
            await self.reply(
                msg,
                build_stt_error_reply(
                    trace_id,
                    request_id,
                    voice_validation_error(outcome.error_code),
                    job_id,
                ),
            )
            return
        req = outcome.request
        assert req is not None and outcome.storage_blob_ref is not None

        if self.reject_when_full:
            try:
                await asyncio.wait_for(self._sem.acquire(), timeout=0)
            except asyncio.TimeoutError:
                await self.reply(
                    msg,
                    build_stt_error_reply(
                        trace_id,
                        req.request_id,
                        voice_validation_error("capacity_exceeded"),
                        job_id,
                    ),
                )
                return
            try:
                await self._run_transcription(
                    msg,
                    req.request_id,
                    outcome.storage_blob_ref,
                    outcome.overrides or {},
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
                    outcome.storage_blob_ref,
                    outcome.overrides or {},
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
                await self.reply(
                    msg,
                    build_stt_error_reply(
                        trace_id,
                        request_id,
                        resolve_worker_error(result, VOICE_STT_RUNNER_ERRORS),
                        job_id,
                    ),
                )
                return
            fields = result  # type: ignore[assignment]
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
