"""Background recorder for NATS-based dictation.

Manages a background recording process with toggle semantics:
- First call starts recording (spawns background process)
- Second call stops recording and returns WAV bytes

State is maintained via files in ~/.local/share/voicecli/:
- nats-recording.json: {pid, started_at, model, language}
- nats-recording.wav: final WAV file (after recording stops)
- nats-recording.wav.partial: during recording
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STATE_DIR = Path.home() / ".local" / "share" / "voicecli"
STATE_FILE = STATE_DIR / "nats-recording.json"
WAV_FILE = STATE_DIR / "nats-recording.wav"
WAV_PARTIAL = STATE_DIR / "nats-recording.wav.partial"

LOG_DIR = Path.home() / ".local" / "state" / "voicecli"
RECORDER_LOG = LOG_DIR / "recorder.log"


def _ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def is_recording() -> bool:
    """Check if a NATS recording is in progress.

    Returns True if state file exists and the PID is still running.
    """
    if not STATE_FILE.exists():
        return False

    try:
        state = json.loads(STATE_FILE.read_text())
        pid = state.get("pid")
        if not pid:
            return False

        # Check if process is still running
        try:
            os.kill(pid, 0)  # Doesn't actually kill, just checks existence
            return True
        except (OSError, ProcessLookupError):
            # Process dead — clean up stale state
            _cleanup_state()
            return False

    except (json.JSONDecodeError, KeyError):
        _cleanup_state()
        return False


def _cleanup_state() -> None:
    """Remove state file and partial WAV."""
    STATE_FILE.unlink(missing_ok=True)
    WAV_PARTIAL.unlink(missing_ok=True)
    WAV_FILE.unlink(missing_ok=True)


def start_recording(
    *, model: str = "large-v3-turbo", language: str | None = None
) -> dict[str, Any]:
    """Start background recording.

    Spawns a subprocess that records from microphone until stopped.
    Returns {"status": "recording", "pid": int} on success.
    Returns {"error": str} on failure.
    """
    if is_recording():
        return {"error": "recording already in progress"}

    _ensure_state_dir()

    # Spawn background recorder process
    # Use the same Python interpreter and voicecli module
    cmd = [
        sys.executable,
        "-m",
        "voicecli.ui.nats_mic_recorder",
        "--run-recorder",
        "--model",
        model,
    ]
    if language:
        cmd.extend(["--language", language])

    log_fh = None
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        log_fh = RECORDER_LOG.open("a")
    except OSError as e:
        log.error("Failed to open recorder log: %s", e)
    try:
        proc = subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=log_fh if log_fh is not None else subprocess.DEVNULL,
        )
    except Exception as e:
        log.error("Failed to spawn recorder: %s", e)
        return {"error": f"cannot start recorder: {e}"}
    finally:
        if log_fh is not None:
            log_fh.close()

    # Give it a moment to write state file
    for _ in range(10):
        time.sleep(0.1)
        if STATE_FILE.exists():
            break

    if not STATE_FILE.exists():
        log.error("Recorder did not write state file — see %s", RECORDER_LOG)
        return {"error": f"recorder failed to start — see {RECORDER_LOG}"}

    return {"status": "recording", "pid": proc.pid}


def stop_recording() -> bytes:
    """Stop background recording and return WAV bytes.

    Sends SIGTERM to the recorder process, waits for it to finish,
    then reads the WAV file.

    Returns empty bytes if no recording was in progress or on error.
    """
    if not is_recording():
        log.warning("stop_recording called but no recording in progress")
        return b""

    try:
        state = json.loads(STATE_FILE.read_text())
        pid = state.get("pid")
    except (json.JSONDecodeError, KeyError):
        log.error("Invalid state file")
        _cleanup_state()
        return b""

    # Send SIGTERM to stop recording gracefully
    try:
        os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        log.warning("Recorder process already dead")
        _cleanup_state()
        return b""

    # Wait for WAV file to appear (up to 5 seconds)
    for _ in range(50):
        time.sleep(0.1)
        if WAV_FILE.exists():
            break

    if not WAV_FILE.exists():
        log.error("WAV file not created after stopping recorder")
        _cleanup_state()
        return b""

    try:
        wav_bytes = WAV_FILE.read_bytes()
    except Exception as e:
        log.error("Failed to read WAV file: %s", e)
        _cleanup_state()
        return b""

    # Clean up
    _cleanup_state()

    return wav_bytes


# ── Background recorder entry point ──────────────────────────────────────────────


def _record_until_signal(stop_event: threading.Event) -> bytes:
    """Record from mic until stop_event is set. Return WAV bytes."""
    # Try pyaudio first, fall back to parecord
    from voicecli.runtime.transcribe_daemon import _probe_pyaudio, _record_parecord, RecordingThread

    if _probe_pyaudio():
        rt = RecordingThread()
        rt.start()
        stop_event.wait()
        return rt.stop()
    else:
        return _record_parecord(stop_event)


def _handle_stop_signal(_signum: int, _frame: Any) -> None:
    """Signal handler to stop recording."""
    global _stop_event
    if _stop_event:
        _stop_event.set()


_stop_event: threading.Event | None = None

PROGRESS_TICK_SECONDS = 1.0


def _progress_notify_loop(stop_event: threading.Event, started_at: float) -> None:
    """Refresh the desktop notification every PROGRESS_TICK_SECONDS with elapsed seconds.

    Runs as a daemon thread inside the recorder subprocess so the user sees
    "Recording... 3s" → "Recording... 5s" updates while speaking, instead of
    a static "Recording..." until they stop. Exits when ``stop_event`` is set.
    """
    from voicecli.ui.dictate_client import notify

    while not stop_event.wait(PROGRESS_TICK_SECONDS):
        elapsed = int(time.monotonic() - started_at)
        notify(f"Recording... {elapsed}s", timeout=0)


def run_recorder_main(*, model: str, language: str | None = None) -> None:
    """Main function for background recorder process.

    1. Sets up signal handlers
    2. Writes state file
    3. Records to partial WAV
    4. On stop: renames to final WAV, cleans up
    """
    global _stop_event

    _ensure_state_dir()

    # Set up signal handlers
    _stop_event = threading.Event()
    signal.signal(signal.SIGTERM, _handle_stop_signal)
    signal.signal(signal.SIGINT, _handle_stop_signal)

    # Write state file
    state = {
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "language": language,
    }
    STATE_FILE.write_text(json.dumps(state))

    # Progress notifier — refreshes the bubble while the user speaks.
    progress_thread = threading.Thread(
        target=_progress_notify_loop,
        args=(_stop_event, time.monotonic()),
        daemon=True,
    )
    progress_thread.start()

    try:
        # Record until signal
        wav_bytes = _record_until_signal(_stop_event)

        # Write WAV file
        WAV_PARTIAL.write_bytes(wav_bytes)
        WAV_PARTIAL.rename(WAV_FILE)

    except Exception as e:
        log.exception("Recorder error: %s", e)
        # Clean up on error
        _cleanup_state()
        sys.exit(1)

    finally:
        # Always clean up state file
        STATE_FILE.unlink(missing_ok=True)


# ── CLI entry point for --run-recorder ───────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--run-recorder", action="store_true", help="Run as background recorder")
    parser.add_argument("--model", default="large-v3-turbo", help="STT model")
    parser.add_argument("--language", default=None, help="Language code")
    args = parser.parse_args()

    if args.run_recorder:
        run_recorder_main(model=args.model, language=args.language)
    else:
        parser.print_help()
