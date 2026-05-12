"""Executor-bound STT transcription run loop extracted from SttNatsAdapter._run_transcription.

This module owns the stateless transcription logic: size-cap, base64 decode,
file write, model warmup, and inference dispatch. Envelope validation is an
orthogonal concern handled by ``voicecli.nats._validation``.

Threading boundary
------------------
``SttRunnerState.set_model_warm`` and ``SttRunnerState.set_model_loaded`` are
callbacks supplied by the adapter. They are invoked from inside
``run_transcription`` *after* the warmup executor call returns — which means
they fire on the async event-loop thread (``run_in_executor`` awaits the
future, then the continuation runs on the loop). The adapter's heartbeat also
reads ``model_loaded`` from the loop thread, so there is no cross-thread
contention. CPython's GIL makes single-attribute writes safe regardless, but
the design keeps all attribute mutations on the loop thread to avoid races.

MAX_AUDIO_B64_LEN
-----------------
The cap is passed in as ``max_audio_b64_len`` rather than imported here. The
named constant ``MAX_AUDIO_B64_LEN`` lives in ``stt_adapter.py`` and is
forwarded by the adapter, keeping the dependency direction adapter → runner.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

from voicecli.nats import tempdir as _tempdir

log = logging.getLogger(__name__)


@dataclass
class SttRunnerState:
    """Mutable state owned by the adapter and threaded through run_transcription."""

    executor: ThreadPoolExecutor
    set_model_warm: Callable[[bool], None]
    set_model_loaded: Callable[[str | None], None]
    model_warm: bool = False


async def run_transcription(
    state: SttRunnerState,
    default_model: str,
    max_audio_b64_len: int,
    payload: dict,
    request_id: str,
    audio_b64: str,
    overrides: dict,
    *,
    trace_id: str,
) -> tuple[bool, dict | str]:
    """Execute one STT transcription request; return ``(ok, result_or_error_code)``.

    On success returns ``(True, {"text": ..., "language": ..., "duration_seconds": ...})``.
    On any failure returns ``(False, "<error_code>")``.

    The adapter is responsible for creating and cleaning up the on-disk audio
    file.  This function writes decoded bytes to ``out_path`` but never calls
    ``cleanup()`` — the adapter wraps this coroutine in ``try/finally``.
    """
    # Deferred imports: avoid pulling torch / faster-whisper at module load time
    # and prevent a circular import (stt_adapter → _stt_runner → stt_adapter
    # would be a load-time cycle; deferred import is safe because stt_adapter
    # only imports this runner after its own module-level init is complete).
    from voicecli.nats.stt_adapter import _duration_from_segments, _ext_from_mime  # noqa: PLC0415

    ext = _ext_from_mime(payload.get("mime_type"))
    out_path = _tempdir.scoped_path(request_id, ext)

    try:
        # Size cap — prevent memory blowup from oversized or misrouted payloads.
        if len(audio_b64) > max_audio_b64_len:
            log.warning(
                "payload_too_large",
                extra={"request_id": request_id, "size": len(audio_b64)},
            )
            return (False, "payload_too_large")

        # Isolate bad-base64 from transcription failures.
        try:
            audio_bytes = base64.b64decode(audio_b64, validate=True)
        except Exception:  # noqa: BLE001
            log.warning("audio_decode_failed", extra={"request_id": request_id})
            return (False, "audio_decode_failed")

        out_path.write_bytes(audio_bytes)
        out_path.chmod(0o600)

        # Idempotent warmup: load the model once and record success via callbacks.
        if not state.model_warm:
            try:
                from voicecli.api import warmup_model  # noqa: PLC0415

                loop = asyncio.get_running_loop()
                await loop.run_in_executor(state.executor, warmup_model, default_model)
                state.set_model_warm(True)
                state.set_model_loaded(default_model)
            except Exception:  # noqa: BLE001
                log.exception("model_load_failed", extra={"request_id": request_id})
                return (False, "model_load_failed")

        from voicecli import api  # noqa: PLC0415

        fn = functools.partial(
            api.transcribe,
            out_path,
            model=default_model,
            _skip_daemon=True,
            **overrides,
        )
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(state.executor, fn)
        except api.ParamValidationError as exc:
            log.warning(
                "param_validation_failed",
                extra={"request_id": request_id, "reason": str(exc)},
            )
            return (False, "param_validation_failed")
        duration_seconds = _duration_from_segments(result.segments)
        return (
            True,
            {
                "text": result.text,
                "language": result.language,
                "duration_seconds": duration_seconds,
            },
        )

    except Exception:  # noqa: BLE001
        log.exception("transcription_failed", extra={"request_id": request_id})
        return (False, "transcription_failed")
