"""STT dictation history — append and read JSONL entries.

History is stored at ``paths.STT_HISTORY_PATH`` (capped at HISTORY_MAX entries).
Each entry is one JSON object per line:
  {"ts": "2024-01-01T12:00:00", "text": "...", "language": "fr",
   "mode": "default", "duration_s": 3.14}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from voicecli.paths import STT_HISTORY_PATH

HISTORY_PATH = STT_HISTORY_PATH  # public alias used by cli.py
HISTORY_MAX = 100


def wav_duration_s(wav_bytes: bytes) -> float | None:
    """Parse WAV header to compute duration in seconds. Returns None on failure."""
    try:
        import io
        import wave

        with wave.open(io.BytesIO(wav_bytes)) as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate > 0:
                return frames / rate
    except Exception:
        pass
    return None


def append_history(
    text: str,
    language: str | None,
    mode: str | None,
    duration_s: float | None,
) -> None:
    """Append one entry to the JSONL history file, capping at HISTORY_MAX entries."""
    from datetime import datetime

    if not text:
        return

    entry = {
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "text": text,
        "language": language,
        "mode": mode,
        "duration_s": round(duration_s, 2) if duration_s is not None else None,
    }

    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        # Trim only when over cap (read-modify-write is rare)
        lines = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
        if len(lines) > HISTORY_MAX:
            HISTORY_PATH.write_text("\n".join(lines[-HISTORY_MAX:]) + "\n", encoding="utf-8")
    except Exception as e:
        print(f"[stt] history write failed: {e}", file=sys.stderr)


def read_history(path: Path | None = None) -> list[dict]:
    """Return all history entries as a list of dicts (oldest first)."""
    p = path or HISTORY_PATH
    if not p.exists():
        return []
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    entries: list[dict] = []
    for ln in lines:
        try:
            entries.append(json.loads(ln))
        except Exception:
            pass
    return entries
