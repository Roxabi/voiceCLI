"""Canonical NATS queue group names for voicecli's worker roles.

Queue groups enforce load balancing within a role during rolling restarts so
that no message is delivered twice to the same logical consumer. Each constant
defines exactly one role → name mapping.
"""

from __future__ import annotations

from roxabi_contracts.voice import SUBJECTS

#: Queue group for TTS worker processes consuming synthesis requests.
TTS_WORKERS = SUBJECTS.tts_workers

#: Queue group for STT worker processes consuming transcription requests.
STT_WORKERS = SUBJECTS.stt_workers
