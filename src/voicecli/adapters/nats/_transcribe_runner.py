"""Executor-bound STT transcription run loop extracted from SttNatsAdapter._run_transcription.

This module owns the stateless transcription logic: BlobStore fetch, file
write, model warmup, and inference dispatch. Envelope validation is an
orthogonal concern handled by ``voicecli.adapters.nats._validation``.

Path-creation ownership
-----------------------
The runner owns scoped-path creation. It derives the file extension from
``blob_ref.mime`` and writes decoded bytes to
``scoped_dir / "{request_id}.{ext}"``. The adapter MUST NOT pre-create a
temp path before calling the runner; it receives ``scoped_path`` back via the
third element of the result tuple and passes it to ``cleanup()`` in its
``finally`` block.

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
"""

from __future__ import annotations

import asyncio
import functools
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from roxabi_blobs import BlobRef

from voicecli.adapters.nats._audio_utils import (
    MAX_AUDIO_BYTES,
    _duration_from_segments,
    _ext_from_mime,
)
from voicecli.adapters.nats.blobs import BlobstoreConfigError

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
    blob_ref: BlobRef,
    request_id: str,
    scoped_dir: Path,
    overrides: dict,
    *,
    trace_id: str,
) -> tuple[bool, dict | str, Path | None]:
    """Execute one STT transcription request.

    Returns ``(ok, result_or_error_code, scoped_path)``.

    On success: ``(True, {"text": ..., "language": ..., "duration_seconds": ...}, scoped_path)``.
    On any failure: ``(False, "<error_code>", None)`` except after the file is
    written, where scoped_path may be non-None so the adapter can clean up.

    The adapter is responsible for calling ``cleanup(scoped_path)`` in a
    ``finally`` block. This function never calls ``cleanup()`` itself.
    """
    from voicecli.adapters.nats.blobs import get_blobstore  # noqa: PLC0415

    # Two-gate size cap: reject obviously-too-large payloads BEFORE the HTTP
    # round-trip (cheap, trusts the remote-supplied size) and again AFTER
    # download (defense against an under-reporting sender). Same byte budget as
    # the pre-V2 audio_b64 pathway (MAX_AUDIO_BYTES from _audio_utils.py).
    if blob_ref.size > MAX_AUDIO_BYTES:
        log.warning(
            "payload_too_large",
            extra={
                "request_id": request_id,
                "blob_size": blob_ref.size,
                "max": MAX_AUDIO_BYTES,
            },
        )
        return (False, "payload_too_large", None)

    # Fetch audio bytes from BlobStore.
    try:
        wav_bytes = await get_blobstore().get(blob_ref.store_key)
    except BlobstoreConfigError as e:
        log.error(
            "blobstore_init_failed",
            extra={"request_id": request_id, "err": str(e)},
        )
        return (False, "blobstore_not_configured", None)
    except Exception as e:  # noqa: BLE001
        log.warning(
            "blobstore_get_failed",
            extra={"request_id": request_id, "err": str(e)},
        )
        return (False, "audio_fetch_failed", None)

    if len(wav_bytes) > MAX_AUDIO_BYTES:
        log.warning(
            "payload_too_large",
            extra={
                "request_id": request_id,
                "actual_bytes": len(wav_bytes),
                "max": MAX_AUDIO_BYTES,
            },
        )
        return (False, "payload_too_large", None)

    # Runner owns scoped-path creation: derive ext from blob_ref.mime.
    ext = _ext_from_mime(blob_ref.mime)
    scoped_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    scoped_dir.chmod(0o700)
    scoped_path = scoped_dir / f"{request_id}.{ext.lstrip('.')}"

    try:
        scoped_path.write_bytes(wav_bytes)
        scoped_path.chmod(0o600)

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
                return (False, "model_load_failed", scoped_path)

        from voicecli import api  # noqa: PLC0415

        fn = functools.partial(
            api.transcribe,
            scoped_path,
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
            return (False, "param_validation_failed", scoped_path)
        duration_seconds = _duration_from_segments(result.segments)
        return (
            True,
            {
                "text": result.text,
                "language": result.language,
                "duration_seconds": duration_seconds,
            },
            scoped_path,
        )

    except Exception:  # noqa: BLE001
        log.exception("transcription_failed", extra={"request_id": request_id})
        return (False, "transcription_failed", scoped_path)
