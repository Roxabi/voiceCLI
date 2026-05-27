"""Canonical NATS queue group names for voicecli's worker roles.

Queue groups enforce load balancing within a role during rolling restarts so
that no message is delivered twice to the same logical consumer. Each constant
defines exactly one role → name mapping.
"""

from __future__ import annotations

#: Queue group for TTS worker processes consuming synthesis requests.
TTS_WORKERS = "tts-workers"

#: Queue group for STT worker processes consuming transcription requests.
STT_WORKERS = "stt-workers"
