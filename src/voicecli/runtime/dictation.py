"""Dictation action handlers for the STT daemon."""

from __future__ import annotations

import gc
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from voicecli.runtime.recording import _save_recording
from voicecli.runtime.wire_protocol import send_json as _send_json
from voicecli.core.history import append_history, wav_duration_s

if TYPE_CHECKING:
    from voicecli.runtime.transcribe_daemon import SttDaemon


def handle_ping(daemon: "SttDaemon", conn) -> None:
    _send_json(conn, {"status": "ok"})


def handle_status(daemon: "SttDaemon", conn) -> None:
    with daemon._lock:
        state = daemon._state.value
        mode = daemon._current_mode or daemon.default_mode
    _send_json(conn, {"status": "ok", "state": state, "mode": mode})


def handle_unknown(daemon: "SttDaemon", conn, action: str | None) -> None:
    _send_json(conn, {"status": "error", "message": f"unknown action: {action}"})


def handle_transcribe_file(daemon: "SttDaemon", conn, req: dict) -> None:
    """Transcribe an audio file using the warm model. No state-machine interaction."""
    audio_path = req.get("audio_path")
    if not audio_path:
        _send_json(conn, {"status": "error", "message": "missing required field: 'audio_path'"})
        return
    path = Path(audio_path)
    if not path.exists():
        _send_json(conn, {"status": "error", "message": f"file not found: {audio_path}"})
        return

    language = req.get("language")
    task = req.get("task", "transcribe")
    initial_prompt = req.get("initial_prompt")
    language_detection_threshold = req.get("language_detection_threshold")
    language_detection_segments = req.get("language_detection_segments")
    language_fallback = req.get("language_fallback")

    # Use daemon-level defaults when caller doesn't specify
    if language_detection_threshold is None:
        language_detection_threshold = daemon.language_detection_threshold
    if language_detection_segments is None:
        language_detection_segments = daemon.language_detection_segments
    if language_fallback is None:
        language_fallback = daemon.language_fallback

    from voicecli.runtime.transcribe import transcribe

    try:
        import torch

        _oom_type: type = torch.cuda.OutOfMemoryError
    except (ImportError, AttributeError):
        _oom_type = type(None)  # never matches — no torch, no OOM handling

    max_retries = 3
    delay = 5
    for attempt in range(1, max_retries + 1):
        try:
            result = transcribe(
                path,
                model=daemon.model,
                language=language,
                language_detection_threshold=language_detection_threshold,
                language_detection_segments=language_detection_segments,
                language_fallback=language_fallback,
                task=task,
                initial_prompt=initial_prompt,
                segment_context_carry=daemon.segment_context_carry,
                _skip_daemon=True,
            )
            _send_json(
                conn,
                {
                    "status": "ok",
                    "text": result.text,
                    "language": result.language,
                    "segments": result.segments,
                },
            )
            return
        except Exception as e:  # noqa: BLE001
            if isinstance(e, _oom_type):
                print(
                    f"[voicecli stt] OOM loading model, retry {attempt}/{max_retries}"
                    f" in {delay}s...",
                    file=sys.stderr,
                )
                gc.collect()
                torch.cuda.empty_cache()
                time.sleep(delay)
                delay *= 2
            else:
                print(f"[stt] transcribe_file error: {e}", file=sys.stderr)
                _send_json(conn, {"status": "error", "message": str(e)})
                return

    _send_json(conn, {"status": "error", "message": f"CUDA OOM after {max_retries} retries"})


def handle_toggle(daemon: "SttDaemon", conn, mode: str | None = None) -> None:
    from voicecli.runtime.transcribe_daemon import State

    with daemon._lock:
        state = daemon._state
    if state == State.IDLE:
        daemon._start_recording(conn, mode=mode)
    elif state == State.RECORDING:
        # _stop_and_transcribe blocks until transcription is done, then sends
        # the response via conn.  conn lifecycle remains managed by _handle's
        # finally block.
        _stop_and_transcribe(daemon, conn)
    elif state == State.TRANSCRIBING:
        daemon._queue_recording(conn)
    elif state == State.QUEUED:
        _send_json(conn, {"status": "ok", "state": State.QUEUED.value})


def handle_cancel(daemon: "SttDaemon", conn) -> None:
    from voicecli.runtime.transcribe_daemon import State

    with daemon._lock:
        state = daemon._state
        if state not in (State.RECORDING, State.QUEUED):
            _send_json(conn, {"status": "ok", "state": State.IDLE.value})
            return
        daemon._state = State.IDLE
        rt = daemon._recording_thread
        daemon._recording_thread = None
        stop_ev = daemon._parecord_stop_event
        daemon._parecord_stop_event = None

    if rt is not None:
        threading.Thread(target=rt.stop, daemon=True).start()
    if stop_ev is not None:
        stop_ev.set()
    _send_json(conn, {"status": "ok", "state": State.IDLE.value})


def handle_next_mode(daemon: "SttDaemon", conn) -> None:
    """Cycle to the next available mode and update default_mode."""
    from voicecli.core.config import _find_config
    from voicecli.core.dictate_modes import load_modes

    cfg_path = _find_config()
    raw_cfg: dict = {}
    if cfg_path:
        import tomllib

        with open(cfg_path, "rb") as f:
            raw_cfg = tomllib.load(f)
    modes = load_modes(raw_cfg)
    mode_names = sorted(modes.keys())
    current = daemon._current_mode or daemon.default_mode
    if current in mode_names:
        idx = (mode_names.index(current) + 1) % len(mode_names)
    else:
        idx = 0
    from voicecli.runtime.transcribe_daemon import State

    next_mode = mode_names[idx]
    daemon.default_mode = next_mode
    daemon._current_mode = next_mode if daemon._state == State.RECORDING else None
    desc = modes[next_mode].get("description", next_mode)
    _send_json(conn, {"status": "ok", "mode": next_mode, "description": desc})


def _stop_and_transcribe(daemon: "SttDaemon", conn) -> None:
    from voicecli.runtime.transcribe_daemon import State

    with daemon._lock:
        daemon._state = State.TRANSCRIBING
        rt = daemon._recording_thread
        daemon._recording_thread = None
        parecord_stop_ev = daemon._parecord_stop_event
        daemon._parecord_stop_event = None
        current_mode = daemon._current_mode
        daemon._current_mode = None

    # Collect WAV bytes from whichever recording path was active
    if rt is not None:
        wav_bytes = rt.stop()
    elif parecord_stop_ev is not None:
        parecord_stop_ev.set()
        parecord_thread = daemon._parecord_thread
        wav_holder = daemon._parecord_wav_holder
        if parecord_thread is not None:
            parecord_thread.join(timeout=3.0)
        wav_bytes = wav_holder[0] if wav_holder else b""
    else:
        wav_bytes = b""

    # Resolve mode params (mode overrides daemon-level settings)
    transcribe_language = daemon.language
    transcribe_task = "transcribe"
    transcribe_prompt: str | None = None
    if current_mode is not None:
        try:
            from voicecli.core.config import load_config
            from voicecli.core.dictate_modes import get_mode

            mode_cfg = get_mode(current_mode, load_config())
            if "language" in mode_cfg:
                transcribe_language = mode_cfg["language"]
            if "task" in mode_cfg:
                transcribe_task = mode_cfg["task"]
            if "prompt" in mode_cfg:
                transcribe_prompt = mode_cfg["prompt"]
        except Exception as e:
            print(f"[stt] mode resolve error: {e}", file=sys.stderr)

    # Prepend personal vocab to the mode prompt (loaded fresh — no restart needed)
    try:
        from voicecli.core.config import load_vocab, vocab_to_prompt

        vocab_fragment = vocab_to_prompt(load_vocab())
        if vocab_fragment:
            transcribe_prompt = (
                vocab_fragment + " " + transcribe_prompt if transcribe_prompt else vocab_fragment
            )
    except Exception as e:
        print(f"[stt] vocab load error: {e}", file=sys.stderr)

    # Lazy imports so test patches are respected
    from voicecli.runtime.recording import _write_tempfile
    from voicecli.ui.clipboard import write_clipboard

    tmp_path = _write_tempfile(wav_bytes)
    text: str = ""
    language: str | None = None
    try:
        from voicecli.runtime.transcribe import transcribe

        result = transcribe(
            tmp_path,
            model=daemon.model,
            language=transcribe_language,
            language_detection_threshold=daemon.language_detection_threshold,
            language_detection_segments=daemon.language_detection_segments,
            language_fallback=daemon.language_fallback,
            task=transcribe_task,
            initial_prompt=transcribe_prompt,
            segment_context_carry=daemon.segment_context_carry,
            _skip_daemon=True,
        )
        text = result.text
        language = result.language
        print(f"[stt] detected language: {language}", file=sys.stderr)
    except Exception as e:
        print(f"[stt] transcription error: {e}", file=sys.stderr)
    finally:
        tmp_path.unlink(missing_ok=True)

    try:
        write_clipboard(text)
    except Exception as e:
        print(f"[stt] clipboard error: {e}", file=sys.stderr)

    if text and daemon.auto_paste:
        from voicecli.ui.clipboard import auto_paste

        threading.Thread(target=auto_paste, daemon=True).start()

    _save_recording(wav_bytes, text, language)

    duration_s = wav_duration_s(wav_bytes)
    append_history(text, language, current_mode, duration_s)

    with daemon._lock:
        queued = daemon._state == State.QUEUED
        daemon._state = State.IDLE

    _send_json(
        conn, {"status": "ok", "state": State.IDLE.value, "text": text, "language": language}
    )

    if queued:
        daemon._start_recording_async()
