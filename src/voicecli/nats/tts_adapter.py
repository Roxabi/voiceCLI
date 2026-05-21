"""TtsNatsAdapter — voicecli NATS satellite for TTS synthesis requests."""

from __future__ import annotations

import asyncio
import base64  # noqa: F401 — test patch anchor for voicecli.nats.tts_adapter.base64.b64encode
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.voice import SUBJECTS as VOICE_SUBJECTS
from roxabi_contracts.voice.models import TtsResponse
from roxabi_nats import NatsAdapterBase
from voicecli.nats._tts_runner import TtsRunnerState, run_synthesis
from voicecli.nats._validation import validate_tts_request
from voicecli.nats.queue_groups import TTS_WORKERS
from voicecli.nats.tempdir import cleanup, scoped_path

# voicecli.api is NOT imported at module level — deferred to keep startup fast
# and avoid pulling torch when only inspecting the adapter (e.g. for --help).

log = logging.getLogger(__name__)


def _safe_reason(exc: BaseException, *, max_len: int = 200) -> str:
    """Sanitize an exception message for safe inclusion in structured logs.

    Escapes \\n/\\r to prevent multi-line log injection and caps length so a
    large user-controlled payload cannot bloat log records.
    """
    return str(exc)[:max_len].replace("\n", "\\n").replace("\r", "\\r")


SUBJECT = VOICE_SUBJECTS.tts_request
HEARTBEAT_SUBJECT = VOICE_SUBJECTS.tts_heartbeat


def _engine_available(engine: str) -> bool:
    from voicecli.engine import _get_registry

    return engine in _get_registry()


def _err_tts(trace_id: str, request_id: str, error: str) -> bytes:
    if not request_id:
        m = TtsResponse.model_construct(
            contract_version=CONTRACT_VERSION,
            trace_id=trace_id,
            issued_at=datetime.now(timezone.utc),
            ok=False,
            request_id="",
            error=error,
        )
    else:
        m = TtsResponse(
            contract_version=CONTRACT_VERSION,
            trace_id=trace_id,
            issued_at=datetime.now(timezone.utc),
            ok=False,
            request_id=request_id,
            error=error,
        )
    return m.model_dump_json(exclude_none=True).encode()


class TtsNatsAdapter(NatsAdapterBase):
    def __init__(
        self,
        *,
        default_engine: str,
        max_concurrent: int = 1,
        reject_when_full: bool = False,
        heartbeat_interval: float = 5.0,
        drain_timeout: float = 30.0,
    ) -> None:
        super().__init__(
            SUBJECT,
            TTS_WORKERS,
            envelope_name="tts",
            schema_version=1,
            drain_timeout=drain_timeout,
            heartbeat_subject=HEARTBEAT_SUBJECT,
            heartbeat_interval=heartbeat_interval,
            inbox_prefix="_inbox.voice-tts",
        )
        self.default_engine = default_engine
        self.max_concurrent = max_concurrent
        self.reject_when_full = reject_when_full
        self.model_loaded: str | None = None
        self._sem = asyncio.Semaphore(max_concurrent)
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent)
        self._runner_state = TtsRunnerState(
            executor=self._executor,
            set_model_loaded=lambda v: setattr(self, "model_loaded", v),
        )

    def heartbeat_payload(self) -> dict:
        from voicecli.model_registry import model_registry

        payload = super().heartbeat_payload()
        payload["model_loaded"] = model_registry.loaded_engines()
        payload["active_requests"] = self.max_concurrent - self._sem._value

        # Add VRAM metrics
        payload["vram_free_mb"] = model_registry.vram_free_mb()
        payload["vram_status"] = model_registry.vram_status()

        return payload

    def _extra_subjects(self) -> list[str]:
        return [f"{self.subject}.{self._worker_id}"]

    async def run(self, nats_url: str, stop: asyncio.Event | None = None) -> None:
        asyncio.create_task(self._prewarm())
        await super().run(nats_url, stop)

    async def _prewarm(self) -> None:
        loop = asyncio.get_running_loop()
        log.info("TTS pre-warm: loading engine=%s", self.default_engine)
        try:
            from voicecli.model_registry import model_registry

            await loop.run_in_executor(self._executor, model_registry.get, self.default_engine)
            log.info("TTS pre-warm complete: engine=%s loaded", self.default_engine)
        except Exception:
            log.warning("TTS pre-warm failed — first request will trigger cold load", exc_info=True)

    async def handle(self, msg: Any, payload: dict) -> None:  # type: ignore[override]
        trace_id = payload.get("trace_id") or "unknown"
        request_id = payload.get("request_id", "")
        if not request_id:
            await self.reply(msg, _err_tts(trace_id, "", "malformed_request"))
            return

        # Reject path-traversal or oversized request IDs at ingestion (Fix 2)
        if not re.match(r"^[A-Za-z0-9_-]{1,128}$", request_id):
            await self.reply(
                msg,
                _err_tts(trace_id, request_id[:64] if request_id else "", "malformed_request"),
            )
            return

        outcome = validate_tts_request(
            payload,
            default_engine=self.default_engine,
            engine_available=_engine_available,
        )
        if outcome.error_code is not None:
            await self.reply(msg, _err_tts(trace_id, request_id, outcome.error_code))
            return
        text = outcome.cleaned_text
        engine = outcome.engine

        if self.reject_when_full:
            # Non-blocking acquire: avoid the race in _sem.locked() (Fix 7)
            try:
                await asyncio.wait_for(self._sem.acquire(), timeout=0)
            except asyncio.TimeoutError:
                await self.reply(msg, _err_tts(trace_id, request_id, "capacity_exceeded"))
                return
            try:
                await self._run_synthesis(msg, payload, request_id, text, engine, trace_id=trace_id)
            finally:
                self._sem.release()
        else:
            async with self._sem:
                await self._run_synthesis(msg, payload, request_id, text, engine, trace_id=trace_id)

    async def _run_synthesis(
        self,
        msg: Any,
        payload: dict,
        request_id: str,
        text: str,
        engine: str,
        *,
        trace_id: str,
    ) -> None:
        out_path = scoped_path(request_id, "wav")
        try:
            ok, result = await run_synthesis(
                self._runner_state,
                payload,
                request_id,
                text,
                engine,
                out_path,
                trace_id=trace_id,
            )
            if not ok:
                # result is the error_code string
                await self.reply(msg, _err_tts(trace_id, request_id, result))  # type: ignore[arg-type]
                return
            # result is the fields dict
            fields = result  # type: ignore[assignment]
            await self.reply(
                msg,
                TtsResponse(
                    contract_version=CONTRACT_VERSION,
                    trace_id=trace_id,
                    issued_at=datetime.now(timezone.utc),
                    ok=True,
                    request_id=request_id,
                    audio_b64=fields["audio_b64"],
                    mime_type=fields["mime_type"],
                    duration_ms=fields["duration_ms"],
                    waveform_b64=fields.get("waveform_b64"),
                )
                .model_dump_json(exclude_none=True)
                .encode(),
            )
        finally:
            cleanup(out_path)
