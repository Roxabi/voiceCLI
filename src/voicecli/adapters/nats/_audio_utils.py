"""STT audio-shape helpers used by both the adapter and the runner.

Extracted from `transcribe_adapter.py` to eliminate the otherwise-required deferred
reverse import in `_transcribe_runner.py`. Owns nothing stateful — pure functions
and one constant lookup table. The adapter re-exports the symbols for
backward-compatible test imports (see `voicecli.adapters.nats.transcribe_adapter`).
"""

from __future__ import annotations

import logging

from voicecli.runtime.transcribe import Segment

log = logging.getLogger(__name__)

# 25 MB base64 → ~18.75 MB decoded audio (~10 min at 8 kHz, ~2 min at 64 kHz).
# Safety cap to prevent memory blowup from crafted or misrouted large payloads.
MAX_AUDIO_B64_LEN = 25 * 1024 * 1024  # 25 MB

_MIME_TO_EXT: dict[str, str] = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mp3": "mp3",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
    "audio/webm": "webm",
}


def _duration_from_segments(segments: list[Segment]) -> float:
    """End timestamp of the last whisper segment; 0.0 on empty list."""
    if not segments:
        return 0.0
    return segments[-1].end


def _ext_from_mime(mime_type: str | None) -> str:
    """File extension for mime_type via _MIME_TO_EXT; 'wav' on unknown/None."""
    if mime_type is None:
        return "wav"
    return _MIME_TO_EXT.get(mime_type.lower().split(";")[0].strip(), "wav")
