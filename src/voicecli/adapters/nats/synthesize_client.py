"""One-shot NATS TTS client for CLI synthesis.

Connects to NATS, sends a synthesis request, and returns the result.
No daemon, no heartbeats — just request/reply.

Mirror of ``transcribe_client.py`` (STT side).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.voice import SUBJECTS as VOICE_SUBJECTS
from roxabi_contracts.voice.models import TtsRequest, TtsResponse
from roxabi_nats import nats_connect

log = logging.getLogger(__name__)

SUBJECT = VOICE_SUBJECTS.tts_request
DEFAULT_TIMEOUT = 60.0


async def synthesize_via_nats(
    text: str,
    *,
    engine: str | None = None,
    voice: str | None = None,
    language: str | None = None,
    chunked: bool = True,
    chunk_size: int | None = None,
    segment_gap: float | None = None,
    crossfade: float | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Send text to NATS TTS satellite, return synthesized audio.

    Args:
        text: Text to synthesize.
        engine: TTS engine name (e.g., ``qwen-fast``). ``None`` → satellite default.
        voice: Voice name. ``None`` → satellite default.
        language: Language name (e.g., ``French``). ``None`` → satellite default.
        chunked: Whether to enable chunked synthesis (satellite-side).
        chunk_size: Target chunk size in characters.
        segment_gap: Silence between segments (seconds).
        crossfade: Fade between segments (seconds).
        timeout: NATS request timeout in seconds.

    Returns:
        On success: ``{"audio": bytes, "mime_type": str, "duration_ms": int}``.
        On failure: ``{"error": str}``.
    """
    nats_url = os.environ.get("NATS_URL")
    if not nats_url:
        return {"error": "NATS_URL environment variable not set"}

    try:
        nc = await nats_connect(nats_url, inbox_prefix="_inbox.voice-client")
    except Exception as e:
        log.error("NATS connection failed: %s", e)
        return {"error": f"Cannot connect to NATS: {e}"}

    try:
        request_id = str(uuid4())

        request = TtsRequest(
            contract_version=CONTRACT_VERSION,
            trace_id=request_id,
            issued_at=datetime.now(timezone.utc),
            request_id=request_id,
            text=text,
            engine=engine,
            voice=voice,
            language=language,
            chunked=chunked,
            chunk_size=chunk_size,
            segment_gap=segment_gap,
            crossfade=crossfade,
        )

        payload = request.model_dump_json(exclude_none=True).encode("utf-8")
        try:
            reply = await nc.request(SUBJECT, payload, timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("NATS TTS request timed out after %ss", timeout)
            return {"error": f"request timed out after {timeout}s"}

        tts_response = TtsResponse.model_validate_json(reply.data)

        if not tts_response.ok:
            return {"error": tts_response.error or "synthesis failed"}

        # V2 contract: response carries blob_ref + metadata; fetch bytes via the
        # BlobStore. Explicit guards rather than `assert` because asserts are
        # stripped under `python -O` — a malformed satellite reply would then
        # silently pass through and trip a misleading AttributeError downstream.
        if tts_response.blob_ref is None:
            return {"error": "malformed_response: missing blob_ref (V2 contract)"}
        if tts_response.mime_type is None:
            return {"error": "malformed_response: missing mime_type"}
        if tts_response.duration_ms is None:
            return {"error": "malformed_response: missing duration_ms"}

        # Blobstore fetch lives outside the NATS-timeout try block above so a
        # HTTP timeout / config error gets a distinct, actionable error code.
        from voicecli.adapters.nats.blobs import (  # noqa: PLC0415
            BlobstoreConfigError,
            get_blobstore,
        )

        try:
            audio_bytes = await get_blobstore().get(tts_response.blob_ref.store_key)
        except BlobstoreConfigError as e:
            log.error("blobstore_init_failed: %s", e)
            return {"error": f"blobstore_not_configured: {e}"}
        except Exception as e:
            log.exception("blobstore_fetch_failed")
            return {"error": f"blobstore_fetch_failed: {e}"}

        return {
            "audio": audio_bytes,
            "mime_type": tts_response.mime_type,
            "duration_ms": tts_response.duration_ms,
        }

    except Exception as e:
        log.exception("NATS TTS request failed")
        return {"error": str(e)}
    finally:
        try:
            await nc.drain()
            await nc.close()
        except Exception:  # noqa: BLE001 — best-effort cleanup; drain/close errors are unactionable here
            log.debug("NATS drain/close error", exc_info=True)
