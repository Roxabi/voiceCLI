"""One-shot NATS STT client for CLI dictation.

Connects to NATS, sends a transcription request, and returns the result.
No daemon, no heartbeats — just request/reply.
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
from roxabi_contracts.voice.models import SttRequest, SttResponse
from roxabi_nats import nats_connect

log = logging.getLogger(__name__)

SUBJECT = VOICE_SUBJECTS.stt_request
DEFAULT_TIMEOUT = 60.0


async def transcribe_via_nats(
    wav_bytes: bytes,
    *,
    model: str = "large-v3-turbo",
    language: str | None = None,
    initial_prompt: str | None = None,
    task: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Send WAV bytes to NATS STT satellite, return result dict.

    Args:
        wav_bytes: Complete WAV file bytes (16kHz mono recommended).
        model: STT model name (e.g., "large-v3-turbo").
        language: Optional language code to force.
        initial_prompt: Whisper decoder context to bias punctuation/casing/vocabulary.
        task: "transcribe" (default on satellite) or "translate".
        timeout: NATS request timeout in seconds.

    Returns:
        {"text": str, "language": str} on success.
        {"error": str} on failure.
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
        audio_b64 = base64.b64encode(wav_bytes).decode("ascii")

        request = SttRequest(
            contract_version=CONTRACT_VERSION,
            trace_id=request_id,
            issued_at=datetime.now(timezone.utc),
            request_id=request_id,
            audio_b64=audio_b64,
            model=model,
            mime_type="audio/wav",
            language=language,
            initial_prompt=initial_prompt,
            task=task,
        )

        payload = request.model_dump_json(exclude_none=True).encode("utf-8")
        reply = await nc.request(SUBJECT, payload, timeout=timeout)
        stt_response = SttResponse.model_validate_json(reply.data)

        if stt_response.ok:
            return {
                "text": stt_response.text or "",
                "language": stt_response.language or "",
            }
        else:
            return {"error": stt_response.error or "transcription failed"}

    except asyncio.TimeoutError:
        log.warning("NATS STT request timed out after %ss", timeout)
        return {"error": f"request timed out after {timeout}s"}
    except Exception as e:
        log.exception("NATS STT request failed")
        return {"error": str(e)}
    finally:
        try:
            await nc.drain()
            await nc.close()
        except Exception:
            pass
