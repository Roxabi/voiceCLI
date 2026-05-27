"""One-shot NATS TTS client for CLI synthesis.

Connects to NATS, sends a synthesis request, and returns the result.
No daemon, no heartbeats — just request/reply.

Mirror of ``transcribe_client.py`` (STT side).
"""

from __future__ import annotations

import asyncio
import base64
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
        reply = await nc.request(SUBJECT, payload, timeout=timeout)
        tts_response = TtsResponse.model_validate_json(reply.data)

        if tts_response.ok:
            assert tts_response.audio_b64 is not None
            assert tts_response.mime_type is not None
            assert tts_response.duration_ms is not None
            return {
                "audio": base64.b64decode(tts_response.audio_b64),
                "mime_type": tts_response.mime_type,
                "duration_ms": tts_response.duration_ms,
            }
        else:
            return {"error": tts_response.error or "synthesis failed"}

    except asyncio.TimeoutError:
        log.warning("NATS TTS request timed out after %ss", timeout)
        return {"error": f"request timed out after {timeout}s"}
    except Exception as e:
        log.exception("NATS TTS request failed")
        return {"error": str(e)}
    finally:
        try:
            await nc.drain()
            await nc.close()
        except Exception:
            pass
