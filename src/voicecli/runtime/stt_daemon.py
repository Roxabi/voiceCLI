"""STT daemon server — keeps faster-whisper warm in VRAM for fast dictation.

Protocol: newline-delimited JSON over AF_UNIX SOCK_STREAM.
Socket path: ~/.local/share/voicecli/stt-daemon.sock
"""

from __future__ import annotations

import os
import socket
import struct
import sys
import threading
from enum import Enum
from pathlib import Path

from roxabi_nats import sanitize_for_wire

from voicecli.core.config import load_stt_config
from voicecli.runtime.wire_protocol import recv_json
from voicecli.runtime.wire_protocol import send_json as _send_json
from voicecli.core.paths import STT_SOCKET_PATH as SOCKET_PATH
from voicecli.ui.sounds import play_ui_sound

from voicecli.runtime.recording import (
    RecordingThread,
    _probe_pyaudio,
    _record_parecord,
)

MAX_MSG = 65536


def _recv_json(sock: socket.socket) -> dict:
    return recv_json(sock, max_msg=MAX_MSG)


LEVEL_FILE = Path("/tmp/voicecli_audio_level")


class State(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    QUEUED = "queued"


def _spawn_overlay(
    mode: str | None = None,
    hotkey: str = "ctrl+space",
    hotkey_cancel: str = "alt+shift+esc",
    hotkey_mode: str = "alt+shift+tab",
) -> None:
    """Launch the waveform overlay from the daemon process (survives WSL session exit)."""
    import subprocess

    env = os.environ.copy()
    if mode:
        env["VOICECLI_OVERLAY_MODE"] = mode
    env["VOICECLI_OVERLAY_HOTKEY_TOGGLE"] = hotkey
    env["VOICECLI_OVERLAY_HOTKEY_CANCEL"] = hotkey_cancel
    env["VOICECLI_OVERLAY_HOTKEY_MODE"] = hotkey_mode
    log = Path(os.environ.get("TMPDIR", "/tmp")) / "voicecli_overlay.log"
    try:
        subprocess.Popen(
            [sys.executable, "-m", "voicecli.ui.overlay"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=open(log, "w"),
            env=env,
        )
    except Exception as e:
        print(f"[stt] overlay spawn failed: {e}", file=sys.stderr)


def warmup(model: str) -> None:
    """Re-exported so tests can patch voicecli.stt_daemon.warmup."""
    from voicecli.runtime.transcribe import warmup as _warmup

    _warmup(model)


class SttDaemon:
    def __init__(
        self,
        model: str = "large-v3-turbo",
        socket_path: Path | None = None,
        language: str | None = None,
        language_detection_threshold: float | None = None,
        language_detection_segments: int | None = None,
        language_fallback: str | None = None,
        default_mode: str | None = None,
        auto_paste: bool = False,
    ):
        self.model = model
        self.language = language
        self.language_detection_threshold = language_detection_threshold
        self.language_detection_segments = language_detection_segments
        self.language_fallback = language_fallback
        self.default_mode = default_mode
        self.auto_paste = auto_paste
        self._socket_path = Path(socket_path) if socket_path is not None else SOCKET_PATH
        self._state = State.IDLE
        self._lock = threading.Lock()
        self._recording_thread: RecordingThread | None = None
        self._use_pyaudio: bool = True  # set by _probe_pyaudio() in serve()
        self._parecord_stop_event: threading.Event | None = None
        self._parecord_thread: threading.Thread | None = None
        self._parecord_wav_holder: list[bytes] = []
        self._connection_sem = threading.BoundedSemaphore(16)
        # Used to shut down the accept loop from outside (tests / stop())
        self._server_socket: socket.socket | None = None
        # Active recording mode (set when recording starts, cleared after transcription)
        self._current_mode: str | None = None
        # Hotkey config loaded once at startup to avoid filesystem walk on every toggle
        _stt_cfg = load_stt_config()
        self._hotkey: str = _stt_cfg.get("hotkey", "ctrl+space")
        self._hotkey_cancel: str = _stt_cfg.get("hotkey_cancel", "alt+shift+esc")
        self._hotkey_mode: str = _stt_cfg.get("hotkey_mode", "alt+shift+tab")

    def stop(self) -> None:
        """Signal the accept loop to exit (used by tests)."""
        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except Exception:
                pass

    def serve(self) -> None:
        self._use_pyaudio = _probe_pyaudio()
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._socket_path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as srv:
            self._server_socket = srv
            srv.bind(str(self._socket_path))
            os.chmod(self._socket_path, 0o600)
            srv.listen(5)
            print(f"[voicecli stt] Ready on {self._socket_path}", flush=True)
            try:
                while True:
                    conn, _ = srv.accept()
                    if self._connection_sem.acquire(blocking=False):

                        def _handle_and_release(c=conn):
                            try:
                                self._handle(c)
                            finally:
                                self._connection_sem.release()

                        threading.Thread(target=_handle_and_release, daemon=True).start()
                    else:
                        # Too many concurrent connections — reject
                        try:
                            _send_json(conn, {"status": "error", "message": "daemon busy"})
                        except Exception:
                            pass
                        conn.close()
            except (KeyboardInterrupt, OSError):
                # OSError is raised when _server_socket is closed by stop()
                pass
            finally:
                # Stop active recording if any
                with self._lock:
                    rt = self._recording_thread
                    self._recording_thread = None
                    stop_ev = self._parecord_stop_event
                    self._parecord_stop_event = None
                if rt is not None:
                    rt._stop_event.set()
                    rt.join(timeout=2.0)
                if stop_ev is not None:
                    stop_ev.set()
                self._socket_path.unlink(missing_ok=True)

    # ── Request dispatch ──────────────────────────────────────────────────────

    def _handle(self, conn: socket.socket) -> None:
        # Verify the connecting peer is the same user who owns the daemon
        try:
            creds = conn.getsockopt(
                socket.SOL_SOCKET,
                socket.SO_PEERCRED,
                struct.calcsize("3i"),
            )
            pid, uid, gid = struct.unpack("3i", creds)
            if uid != os.getuid():
                _send_json(conn, {"status": "error", "message": "permission denied"})
                conn.close()
                return
        except Exception:
            pass  # SO_PEERCRED unavailable (non-Linux) — skip check
        try:
            from voicecli.runtime.dictation import (
                handle_cancel,
                handle_next_mode,
                handle_ping,
                handle_status,
                handle_toggle,
                handle_transcribe_file,
                handle_unknown,
            )

            req = _recv_json(conn)
            action = req.get("action")
            mode = req.get("mode") or None
            if action == "ping":
                handle_ping(self, conn)
            elif action == "status":
                handle_status(self, conn)
            elif action == "toggle":
                handle_toggle(self, conn, mode=mode)
            elif action == "cancel":
                handle_cancel(self, conn)
            elif action == "next_mode":
                handle_next_mode(self, conn)
            elif action == "transcribe_file":
                handle_transcribe_file(self, conn, req)
            else:
                handle_unknown(self, conn, action)
        except Exception as exc:
            try:
                _send_json(conn, {"status": "error", "message": sanitize_for_wire(exc)})
            except Exception:
                pass
        finally:
            conn.close()

    def _start_parecord_recording(self, level_callback=None) -> None:
        """Start parecord subprocess recording. Must be called with self._lock held."""
        stop_ev = threading.Event()
        self._parecord_stop_event = stop_ev
        wav_holder: list[bytes] = []
        self._parecord_wav_holder = wav_holder

        def _run_parecord() -> None:
            wav_holder.append(_record_parecord(stop_ev, level_callback=level_callback))

        t = threading.Thread(target=_run_parecord, daemon=True)
        t.start()
        self._parecord_thread = t

    def _start_recording(self, conn: socket.socket, mode: str | None = None) -> None:
        # Resolve effective mode: request mode > default_mode
        effective_mode = mode if mode is not None else self.default_mode

        def _write_level(level: float) -> None:
            try:
                LEVEL_FILE.write_text(f"{level:.4f}")
            except Exception:
                pass

        with self._lock:
            self._state = State.RECORDING
            self._current_mode = effective_mode
            if self._use_pyaudio:
                self._recording_thread = RecordingThread(level_callback=_write_level)
                self._parecord_stop_event = None
                self._parecord_thread = None
                self._parecord_wav_holder = []
            else:
                self._recording_thread = None
                self._start_parecord_recording(level_callback=_write_level)
        if self._recording_thread:
            self._recording_thread.start()
        threading.Thread(target=play_ui_sound, args=("start.wav",), daemon=True).start()
        threading.Thread(
            target=_spawn_overlay,
            args=(effective_mode, self._hotkey, self._hotkey_cancel, self._hotkey_mode),
            daemon=True,
        ).start()
        _send_json(conn, {"status": "ok", "state": State.RECORDING.value})

    def _queue_recording(self, conn: socket.socket) -> None:
        with self._lock:
            self._state = State.QUEUED
        _send_json(conn, {"status": "ok", "state": State.QUEUED.value})

    def _start_recording_async(self) -> None:
        """Start a new recording after queued transcription completes."""
        with self._lock:
            self._state = State.RECORDING
            if self._use_pyaudio:
                self._recording_thread = RecordingThread()
                self._parecord_stop_event = None
                self._parecord_thread = None
                self._parecord_wav_holder = []
            else:
                self._recording_thread = None
                self._start_parecord_recording()

        if self._recording_thread:
            self._recording_thread.start()
