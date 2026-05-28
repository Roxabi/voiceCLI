"""TtsNatsAdapter — voicecli NATS satellite for TTS synthesis requests."""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.voice.models import TtsResponse
from roxabi_nats import NatsAdapterBase
from roxabi_nats._validate import validate_nats_token
from voicecli.nats.queue_groups import TTS_WORKERS
from voicecli.nats.tempdir import cleanup, scoped_path
from voicecli.nats.tts_wav_utils import (
    cleanup_chunks,
    collect_chunked_output,
    concat_wav_chunks,
    wav_duration_ms,
    wav_waveform_b64,
)

# voicecli.api is NOT imported at module level — deferred to keep startup fast
# and avoid pulling torch when only inspecting the adapter (e.g. for --help).

log = logging.getLogger(__name__)

SUBJECT = "lyra.voice.tts.request"
HEARTBEAT_SUBJECT = "lyra.voice.tts.heartbeat"


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
            inbox_prefix="_INBOX.voice-tts",
        )
        self.default_engine = default_engine
        self.max_concurrent = max_concurrent
        self.reject_when_full = reject_when_full
        self.model_loaded: str | None = None
        self._sem = asyncio.Semaphore(max_concurrent)
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent)

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

        # Validate text field early to avoid KeyError being masked as synthesis_failed (Fix 8)
        text = payload.get("text")
        if not text or not isinstance(text, str):
            await self.reply(msg, _err_tts(trace_id, request_id, "malformed_request"))
            return

        engine = payload.get("engine") or self.default_engine
        try:
            validate_nats_token(engine, kind="engine")
        except ValueError:
            await self.reply(msg, _err_tts(trace_id, request_id, "malformed_request"))
            return
        if not _engine_available(engine):
            await self.reply(msg, _err_tts(trace_id, request_id, "engine_unavailable"))
            return

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
            self.model_loaded = engine
            from voicecli import api

            # Engine-agnostic kwargs forwarded through api.generate **kwargs
            # (translate.py will strip fields the target engine can't consume).
            optional_kwargs = {
                k: v
                for k, v in {
                    "language": payload.get("language"),
                    "voice": payload.get("voice"),
                    "speed": payload.get("speed"),
                    "exaggeration": payload.get("exaggeration"),
                    "cfg_weight": payload.get("cfg_weight"),
                    "accent": payload.get("accent"),
                    "personality": payload.get("personality"),
                    "emotion": payload.get("emotion"),
                }.items()
                if v is not None
            }

            # Named parameters of api.generate — must be passed explicitly, not via **kwargs.
            named_kwargs: dict[str, Any] = {}
            chunked = payload.get("chunked")
            if chunked is not None:
                named_kwargs["chunked"] = bool(chunked)
            for key in ("chunk_size", "segment_gap", "crossfade"):
                value = payload.get(key)
                if value is not None:
                    named_kwargs[key] = value

            loop = asyncio.get_running_loop()

            def _synthesize(language: str | None) -> None:
                from voicecli.utils import UNRESTRICTED

                kw = dict(optional_kwargs)
                if language is not None:
                    kw["language"] = language
                api.generate(
                    text,
                    engine=engine,
                    output=out_path,
                    allowed_base=UNRESTRICTED,
                    _skip_daemon=True,
                    **kw,
                    **named_kwargs,
                )

            try:
                await loop.run_in_executor(self._executor, _synthesize, None)
            except ValueError as exc:
                # ADR-044 fallback_language semantics: api.generate raises ValueError for
                # param/language validation; retry once with the fallback before giving up.
                fallback_language = payload.get("fallback_language")
                primary_language = payload.get("language")
                if fallback_language and fallback_language != primary_language:
                    log.warning(
                        "language_synthesis_failed_retrying_with_fallback",
                        extra={
                            "request_id": request_id,
                            "primary_language": primary_language,
                            "fallback_language": fallback_language,
                            "error": str(exc),
                        },
                    )
                    await loop.run_in_executor(self._executor, _synthesize, fallback_language)
                else:
                    raise

            # If the engine ran in chunked mode it writes {stem}_NNN.wav files
            # plus a {stem}.done sentinel instead of {stem}.wav directly.
            # Detect and concatenate chunks into out_path before encoding.
            chunks = collect_chunked_output(out_path)
            if chunks:
                # issue #60: explicitly tighten each chunk before it is read or
                # deleted, so chunk confidentiality does not rely solely on umask.
                for c in chunks:
                    c.chmod(0o600)
                concat_wav_chunks(chunks, out_path)
                cleanup_chunks(out_path, chunks)

            out_path.chmod(0o600)  # issue #60: belt-and-suspenders over umask 0o077
            audio_b64 = base64.b64encode(out_path.read_bytes()).decode("ascii")
            duration_ms = wav_duration_ms(out_path)
            waveform_b64 = wav_waveform_b64(out_path)
            reply_fields: dict[str, Any] = {
                "audio_b64": audio_b64,
                "mime_type": "audio/wav",
                "duration_ms": duration_ms,
            }
            if waveform_b64 is not None:
                reply_fields["waveform_b64"] = waveform_b64
            await self.reply(
                msg,
                TtsResponse(
                    contract_version=CONTRACT_VERSION,
                    trace_id=trace_id,
                    issued_at=datetime.now(timezone.utc),
                    ok=True,
                    request_id=request_id,
                    audio_b64=audio_b64,
                    mime_type="audio/wav",
                    duration_ms=duration_ms,
                    waveform_b64=waveform_b64,
                )
                .model_dump_json(exclude_none=True)
                .encode(),
            )
        except Exception:
            log.exception("synthesis_failed", extra={"request_id": request_id})
            await self.reply(msg, _err_tts(trace_id, request_id, "synthesis_failed"))
        finally:
            cleanup(out_path)
