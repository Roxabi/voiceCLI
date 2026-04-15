"""SttNatsAdapter — voicecli NATS satellite for STT transcription requests."""

from __future__ import annotations

import asyncio
import base64
import functools
import logging
import os
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from voicecli.nats.base import NatsAdapterBase
from voicecli.nats.queue_groups import STT_WORKERS
from voicecli.nats.reply import build_reply
from voicecli.nats.tempdir import cleanup, scoped_path

# voicecli.api is NOT imported at module level — deferred to keep startup fast
# and avoid pulling torch/faster-whisper when only inspecting the adapter (e.g. --help).

log = logging.getLogger(__name__)

SUBJECT = "lyra.voice.stt.request"

# 25 MB base64 → ~18.75 MB decoded audio (~10 min at 8 kHz, ~2 min at 64 kHz).
# Safety cap to prevent memory blowup from crafted or misrouted large payloads.
MAX_AUDIO_B64_LEN = 25 * 1024 * 1024  # 25 MB
HEARTBEAT_SUBJECT = "lyra.voice.stt.heartbeat"

_MIME_TO_EXT: dict[str, str] = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mp3": "mp3",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
    "audio/webm": "webm",
}


def _duration_from_segments(segments: list[dict]) -> float:
    """Compute duration in seconds from whisper segment timestamps.

    Returns the `end` timestamp of the last segment, or 0.0 if no segments
    (silent audio or detection failure)."""
    if not segments:
        return 0.0
    last = segments[-1]
    if "end" not in last:
        log.warning("segment_missing_end_key", extra={"segments_count": len(segments)})
        return 0.0
    end = last["end"]
    try:
        return float(end)
    except (TypeError, ValueError):
        log.warning(
            "segment_end_not_numeric",
            extra={"segments_count": len(segments), "end_type": type(end).__name__},
        )
        return 0.0


def _ext_from_mime(mime_type: str | None) -> str:
    """Derive file extension from mime_type.

    Default 'wav'. Known mappings:
      audio/wav → wav, audio/x-wav → wav,
      audio/mp3 → mp3, audio/mpeg → mp3,
      audio/ogg → ogg, audio/flac → flac, audio/webm → webm.
    Unknown/None → 'wav'.
    """
    if mime_type is None:
        return "wav"
    return _MIME_TO_EXT.get(mime_type.lower().split(";")[0].strip(), "wav")


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
        worker_id = f"stt-{socket.gethostname()}-{os.getpid()}-{int(time.time())}"
        super().__init__(
            subject=SUBJECT,
            queue_group=STT_WORKERS,
            heartbeat_subject=HEARTBEAT_SUBJECT,
            service="stt_workers",
            worker_id=worker_id,
            heartbeat_interval=heartbeat_interval,
            drain_timeout=drain_timeout,
        )
        self.default_model = default_model
        self.max_concurrent = max_concurrent
        self.reject_when_full = reject_when_full
        self._sem = asyncio.Semaphore(max_concurrent)
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent)
        self._model_warm: bool = False

    async def handle(self, msg: Any, payload: dict) -> None:  # type: ignore[override]
        request_id = payload.get("request_id", "")
        if not request_id:
            await self.reply(msg, build_reply(ok=False, request_id="", error="malformed_request"))
            return

        # Reject path-traversal or oversized request IDs at ingestion
        if not re.match(r"^[A-Za-z0-9_-]{1,128}$", request_id):
            await self.reply(
                msg,
                build_reply(
                    ok=False,
                    request_id=request_id[:64] if request_id else "",
                    error="malformed_request",
                ),
            )
            return

        audio_b64 = payload.get("audio_b64")
        if not audio_b64 or not isinstance(audio_b64, str):
            await self.reply(
                msg, build_reply(ok=False, request_id=request_id, error="malformed_request")
            )
            return

        for key, expected_types in (
            ("language", (str,)),
            ("language_detection_threshold", (int, float)),
            ("language_detection_segments", (int,)),
            ("language_fallback", (str,)),
        ):
            val = payload.get(key)
            if val is None:
                continue
            if key == "language_detection_segments" and isinstance(val, bool):
                # bool is a subclass of int in Python; reject separately
                await self.reply(
                    msg, build_reply(ok=False, request_id=request_id, error="malformed_request")
                )
                return
            if not isinstance(val, expected_types):
                await self.reply(
                    msg, build_reply(ok=False, request_id=request_id, error="malformed_request")
                )
                return

        overrides = {
            k: v
            for k, v in {
                "language": payload.get("language"),
                "language_detection_threshold": payload.get("language_detection_threshold"),
                "language_detection_segments": payload.get("language_detection_segments"),
                "language_fallback": payload.get("language_fallback"),
            }.items()
            if v is not None
        }

        if self.reject_when_full:
            # Non-blocking acquire: avoid the race in _sem.locked()
            try:
                await asyncio.wait_for(self._sem.acquire(), timeout=0)
            except asyncio.TimeoutError:
                await self.reply(
                    msg, build_reply(ok=False, request_id=request_id, error="capacity_exceeded")
                )
                return
            try:
                await self._run_transcription(msg, payload, request_id, audio_b64, overrides)
            finally:
                self._sem.release()
        else:
            async with self._sem:
                await self._run_transcription(msg, payload, request_id, audio_b64, overrides)

    async def _run_transcription(
        self,
        msg: Any,
        payload: dict,
        request_id: str,
        audio_b64: str,
        overrides: dict,
    ) -> None:
        ext = _ext_from_mime(payload.get("mime_type"))
        out_path = scoped_path(request_id, ext)
        try:
            # Fix #2: cap payload size before decode to prevent memory blowup
            if len(audio_b64) > MAX_AUDIO_B64_LEN:
                log.warning(
                    "payload_too_large",
                    extra={"request_id": request_id, "size": len(audio_b64)},
                )
                await self.reply(
                    msg,
                    build_reply(ok=False, request_id=request_id, error="payload_too_large"),
                )
                cleanup(out_path)
                return

            # Decode audio bytes first — isolate bad-base64 from transcription failures
            try:
                audio_bytes = base64.b64decode(audio_b64, validate=True)
            except Exception:
                log.warning("audio_decode_failed", extra={"request_id": request_id})
                await self.reply(
                    msg,
                    build_reply(ok=False, request_id=request_id, error="audio_decode_failed"),
                )
                # Fix #4: remove redundant cleanup(out_path) here; finally block handles it
                return

            out_path.write_bytes(audio_bytes)

            # Fix #1: warm up the model once; distinguish load failures from inference failures.
            # _load_model() handles the mock env-gate short-circuit internally.
            if not self._model_warm:
                try:
                    from voicecli.transcribe import _load_model

                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(self._executor, _load_model, self.default_model)
                    self._model_warm = True
                    # Fix #3: set model_loaded only after the model is actually warm
                    self.model_loaded = self.default_model
                except Exception:
                    log.exception("model_load_failed", extra={"request_id": request_id})
                    await self.reply(
                        msg,
                        build_reply(ok=False, request_id=request_id, error="model_load_failed"),
                    )
                    return

            from voicecli import api

            fn = functools.partial(
                api.transcribe,
                out_path,
                model=self.default_model,
                _skip_daemon=True,
                **overrides,
            )
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(self._executor, fn)
            duration_seconds = _duration_from_segments(result.segments)
            await self.reply(
                msg,
                build_reply(
                    ok=True,
                    request_id=request_id,
                    text=result.text,
                    language=result.language,
                    duration_seconds=duration_seconds,
                ),
            )
        except Exception:
            log.exception("transcription_failed", extra={"request_id": request_id})
            await self.reply(
                msg,
                build_reply(ok=False, request_id=request_id, error="transcription_failed"),
            )
        finally:
            cleanup(out_path)
