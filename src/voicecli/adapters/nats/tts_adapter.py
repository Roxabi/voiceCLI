"""TtsNatsAdapter — voicecli NATS satellite for TTS synthesis requests."""

from __future__ import annotations

import asyncio
import base64  # noqa: F401 — test patch anchor for voicecli.adapters.nats.tts_adapter.base64.b64encode
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.voice import SUBJECTS as VOICE_SUBJECTS
from roxabi_contracts.voice.models import TtsResponse
from roxabi_nats import NatsAdapterBase
from voicecli.adapters.nats._tts_runner import TtsRunnerState, run_synthesis
from voicecli.adapters.nats._validation import validate_tts_request
from voicecli.adapters.nats.queue_groups import TTS_WORKERS
from voicecli.adapters.nats.tempdir import cleanup, scoped_path

# voicecli.api is NOT imported at module level — deferred to keep startup fast
# and avoid pulling torch when only inspecting the adapter (e.g. for --help).

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TtsRequest:
    """Typed TTS request constructed from NATS payload dict."""

    text: str
    request_id: str
    trace_id: str = ""
    contract_version: str = ""
    engine: str = ""
    language: str | None = None
    voice: str | None = None
    speed: float | None = None
    exaggeration: float | None = None
    cfg_weight: float | None = None
    accent: str | None = None
    personality: str | None = None
    emotion: str | None = None
    chunked: bool | None = None
    chunk_size: int | None = None
    segment_gap: float | None = None
    crossfade: float | None = None
    fallback_language: str | None = None

    @classmethod
    def from_payload(cls, payload: dict) -> "TtsRequest":
        """Construct from decoded JSON payload; raise ValueError on type mismatch."""
        text = payload.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("text must be a non-empty str")

        request_id = payload.get("request_id", "")
        if not request_id:
            raise ValueError("request_id is required")

        engine = payload.get("engine", "")

        speed = payload.get("speed")
        if speed is not None and not isinstance(speed, (int, float)):
            raise ValueError("speed must be int or float")

        exaggeration = payload.get("exaggeration")
        if exaggeration is not None and not isinstance(exaggeration, (int, float)):
            raise ValueError("exaggeration must be int or float")

        cfg_weight = payload.get("cfg_weight")
        if cfg_weight is not None and not isinstance(cfg_weight, (int, float)):
            raise ValueError("cfg_weight must be int or float")

        chunk_size = payload.get("chunk_size")
        if chunk_size is not None and not isinstance(chunk_size, int):
            raise ValueError("chunk_size must be an int")

        segment_gap = payload.get("segment_gap")
        if segment_gap is not None and not isinstance(segment_gap, (int, float)):
            raise ValueError("segment_gap must be int or float")

        crossfade = payload.get("crossfade")
        if crossfade is not None and not isinstance(crossfade, (int, float)):
            raise ValueError("crossfade must be int or float")

        chunked = payload.get("chunked")
        if chunked is not None and not isinstance(chunked, bool):
            raise ValueError("chunked must be a bool")

        return cls(
            text=text,
            request_id=request_id,
            trace_id=payload.get("trace_id") or "",
            contract_version=payload.get("contract_version", ""),
            engine=engine,
            language=payload.get("language"),
            voice=payload.get("voice"),
            speed=float(speed) if speed is not None else None,
            exaggeration=float(exaggeration) if exaggeration is not None else None,
            cfg_weight=float(cfg_weight) if cfg_weight is not None else None,
            accent=payload.get("accent"),
            personality=payload.get("personality"),
            emotion=payload.get("emotion"),
            chunked=chunked,
            chunk_size=chunk_size,
            segment_gap=float(segment_gap) if segment_gap is not None else None,
            crossfade=float(crossfade) if crossfade is not None else None,
            fallback_language=payload.get("fallback_language"),
        )


SUBJECT = VOICE_SUBJECTS.tts_request
HEARTBEAT_SUBJECT = VOICE_SUBJECTS.tts_heartbeat


def _engine_available(engine: str) -> bool:
    from voicecli.engines.engine import _get_registry

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
        from voicecli.runtime.model_registry import model_registry

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
            from voicecli.runtime.model_registry import model_registry

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
