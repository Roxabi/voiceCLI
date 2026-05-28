"""Executor-bound TTS synthesis run loop extracted from TtsNatsAdapter._run_synthesis.

State purpose:
    TtsRunnerState bundles the ThreadPoolExecutor and a callback for recording
    which engine is active. Extracting the synthesis loop here keeps TtsNatsAdapter
    focused on NATS I/O while this module owns the generate → encode → return cycle.

Cross-reference:
    voicecli.adapters.nats._validation — envelope validation (orthogonal concern; caller
    validates before invoking run_synthesis).

Threading constraint:
    set_model_loaded is called from run_synthesis on the async event-loop thread,
    before the executor is engaged.  The adapter reads model_loaded from the same
    async loop (heartbeat), so no cross-thread access occurs.  Callers that swap
    the callback for one that writes from an executor thread are violating this
    contract — wrap the write in an asyncio.Event or a thread-safe primitive.

Single-source-of-truth note:
    OPTIONAL_KWARGS and NAMED_KWARGS define exactly which adapter-payload fields
    are forwarded to api.generate.  Adding a new optional adapter kwarg is a no-op
    unless added here.  DO NOT delete these constants — tests import them directly.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from roxabi_contracts.errors import WorkerError

from voicecli.adapters.nats.wav_utils import (
    cleanup_chunks,
    collect_chunked_output,
    concat_wav_chunks,
    wav_duration_ms,
    wav_waveform_b64,
)

log = logging.getLogger(__name__)

OPTIONAL_KWARGS = (
    "language",
    "voice",
    "speed",
    "exaggeration",
    "cfg_weight",
    "accent",
    "personality",
    "emotion",
)
NAMED_KWARGS = ("chunked", "chunk_size", "segment_gap", "crossfade")


@dataclass
class TtsRunnerState:
    executor: ThreadPoolExecutor
    set_model_loaded: Callable[[str], None]


async def run_synthesis(
    state: TtsRunnerState,
    payload: dict,
    request_id: str,
    text: str,
    engine: str,
    out_path: Path,
    *,
    trace_id: str,
) -> tuple[bool, dict | WorkerError]:
    """Run a single TTS synthesis request on the state executor.

    Returns (True, fields_dict) on success or (False, WorkerError) on failure.
    The caller (TtsNatsAdapter) owns out_path cleanup via a finally block; this
    function never deletes out_path.
    """
    # Deferred import so we can reference api.ParamValidationError in the except
    # clause below without pulling torch at module load.
    import voicecli.api as api  # noqa: PLC0415

    try:
        state.set_model_loaded(engine)

        # Engine-agnostic kwargs forwarded through api.generate **kwargs.
        # translate.py strips fields the target engine cannot consume.
        optional_kwargs: dict[str, Any] = {
            k: v for k, v in {k: payload.get(k) for k in OPTIONAL_KWARGS}.items() if v is not None
        }

        # Named parameters of api.generate — must be passed explicitly.
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
            # All heavy imports deferred — keeps startup fast and avoids
            # pulling torch when only inspecting the adapter (e.g. --help).
            from voicecli.adapters.synthesis import LocalSynthesisAdapter  # noqa: PLC0415
            from voicecli.runtime.model_registry import model_registry  # noqa: PLC0415
            from voicecli.core.utils import UNRESTRICTED  # noqa: PLC0415

            kw = dict(optional_kwargs)
            if language is not None:
                kw["language"] = language
            api.generate(
                text,
                engine=engine,
                output=out_path,
                allowed_base=UNRESTRICTED,
                _synthesis=LocalSynthesisAdapter(model_registry),
                **kw,
                **named_kwargs,
            )

        try:
            await loop.run_in_executor(state.executor, _synthesize, None)
        except api.ParamValidationError as exc:
            fallback_language = payload.get("fallback_language")
            primary_language = payload.get("language")
            if fallback_language and fallback_language != primary_language:
                log.warning(
                    "language_synthesis_failed_retrying_with_fallback",
                    extra={
                        "request_id": request_id,
                        "primary_language": primary_language,
                        "fallback_language": fallback_language,
                        "error": str(exc)[:200],
                    },
                )
                try:
                    await loop.run_in_executor(state.executor, _synthesize, fallback_language)
                except api.ParamValidationError as fallback_exc:
                    log.warning(
                        "param_validation_failed",
                        extra={
                            "request_id": request_id,
                            "reason": str(fallback_exc)[:200],
                            "after_fallback": True,
                        },
                    )
                    return False, WorkerError(
                        code="param_validation_failed",
                        message="TTS parameter validation failed",
                        retryable=False,
                    )
            else:
                log.warning(
                    "param_validation_failed",
                    extra={"request_id": request_id, "reason": str(exc)[:200]},
                )
                return False, WorkerError(
                    code="param_validation_failed",
                    message="TTS parameter validation failed",
                    retryable=False,
                )
        except ValueError as exc:
            msg_str = str(exc)
            if msg_str.startswith("Unknown voice"):
                # Extract voice name from the error message for the user-facing message.
                # Full message: "Unknown voice 'Cherry'. Available: [...]"
                # detail carries the available speakers so the caller can pick one.
                log.warning(
                    "unknown_voice",
                    extra={"request_id": request_id, "error": msg_str[:400]},
                )
                return False, WorkerError(
                    code="unknown_voice",
                    message=msg_str.split(".")[0],  # "Unknown voice 'Cherry'"
                    retryable=False,
                    detail=msg_str,
                )
            # Non-"Unknown voice" ValueError — treat as generic synthesis failure.
            log.exception("synthesis_failed", extra={"request_id": request_id})
            return False, WorkerError(
                code="synthesis_failed",
                message=type(exc).__name__,
                retryable=False,
            )

        # If the engine ran in chunked mode it writes {stem}_NNN.wav files
        # plus a {stem}.done sentinel instead of {stem}.wav directly.
        chunks = collect_chunked_output(out_path)
        if chunks:
            for c in chunks:
                c.chmod(0o600)
            concat_wav_chunks(chunks, out_path)
            cleanup_chunks(out_path, chunks)

        out_path.chmod(0o600)
        duration_ms = wav_duration_ms(out_path)
        waveform_b64 = wav_waveform_b64(out_path)

        from voicecli.adapters.nats.blobs import (  # noqa: PLC0415
            BlobRefValidationError,
            BlobstoreConfigError,
            blob_ref_to_contract,
            get_blobstore,
        )

        try:
            blobs_ref = await get_blobstore().put(
                out_path.read_bytes(),
                mime="audio/wav",
                source="voicecli",
            )
        except BlobstoreConfigError as e:
            log.error(
                "blobstore_init_failed",
                extra={"request_id": request_id, "err": str(e)},
            )
            return False, WorkerError(
                code="blobstore_not_configured",
                message="BlobStore not configured",
                retryable=False,
            )
        except Exception as e:
            log.warning(
                "blobstore_put_failed",
                extra={"request_id": request_id, "err": str(e)},
            )
            return False, WorkerError(
                code="audio_store_failed",
                message="Audio storage failed",
                retryable=True,
            )

        # Bridge roxabi_blobs.BlobRef → roxabi_contracts.BlobRef via the helper
        # centralised in blobs.py (single source of truth for the producer-only
        # field exclusion set).
        try:
            fields: dict[str, Any] = {
                "blob_ref": blob_ref_to_contract(blobs_ref).model_dump(),
                "mime_type": "audio/wav",
                "duration_ms": duration_ms,
            }
        except BlobRefValidationError as e:
            log.warning(
                "blobstore_ref_invalid",
                extra={"request_id": request_id, "err": str(e)},
            )
            return False, WorkerError(
                code="audio_store_failed",
                message="Audio storage reference invalid",
                retryable=True,
            )
        if waveform_b64 is not None:
            fields["waveform_b64"] = waveform_b64

        return True, fields

    except Exception as exc:
        log.exception("synthesis_failed", extra={"request_id": request_id})
        return False, WorkerError(
            code="synthesis_failed",
            message=type(exc).__name__,
            retryable=False,
        )
