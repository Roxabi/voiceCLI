"""Audio helpers for the waveform overlay.

Handles UI sound playback and microphone level file reads.
No GTK dependency — safe to unit-test in isolation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from voicecli.runtime.stt_daemon import LEVEL_FILE

_ASSETS = Path(__file__).parent / "assets"

SND_START = _ASSETS / "start.wav"
SND_STOP = _ASSETS / "stop.wav"

LEVEL_PEAK = 0.06


def play(path: Path) -> None:
    """Play a WAV file non-blocking via paplay (fire-and-forget)."""
    if path.exists():
        subprocess.Popen(
            ["paplay", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )


def read_level() -> float:
    """Read the current audio level written by the STT daemon."""
    try:
        return float(LEVEL_FILE.read_text().strip())
    except Exception:
        return 0.0
