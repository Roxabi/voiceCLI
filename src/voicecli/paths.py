"""Centralized path constants for voicecli runtime files.

All socket paths and shared runtime locations are defined here to avoid
duplicated ``Path.home() / ...`` expressions scattered across modules.
"""

from __future__ import annotations

from pathlib import Path

_RUNTIME_DIR = Path.home() / ".local" / "share" / "voicecli"

#: Unix socket for the TTS daemon (daemon.py / api.py).
TTS_SOCKET_PATH = _RUNTIME_DIR / "daemon.sock"

#: Unix socket for the STT daemon (stt_daemon.py / transcribe.py).
STT_SOCKET_PATH = _RUNTIME_DIR / "stt-daemon.sock"

#: JSONL history file for STT dictation entries.
STT_HISTORY_PATH = _RUNTIME_DIR / "stt-history.jsonl"
