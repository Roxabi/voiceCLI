"""STT daemon server — keeps faster-whisper warm in VRAM for fast dictation.

Protocol: newline-delimited JSON over AF_UNIX SOCK_STREAM.
Socket path: ~/.local/share/voicecli/stt-daemon.sock

Actions:
  ping   — liveness check
  status — return current state
  toggle — start recording (idle→recording) or stop+transcribe (recording→transcribing→idle)
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from enum import Enum
from pathlib import Path

SOCKET_PATH = Path.home() / ".local" / "share" / "voicecli" / "stt-daemon.sock"


# ── State machine ─────────────────────────────────────────────────────────────


class State(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    QUEUED = "queued"


# ── pyaudio probe ─────────────────────────────────────────────────────────────


def _probe_pyaudio() -> bool:
    """Return True if pyaudio is usable; print warning and return False otherwise."""
    try:
        import pyaudio

        pa = pyaudio.PyAudio()
        pa.get_device_count()
        pa.terminate()
        return True
    except Exception as e:
        print(f"[stt] pyaudio unavailable ({e}), falling back to parecord", file=sys.stderr)
        return False


# ── WAV helpers ───────────────────────────────────────────────────────────────


def _frames_to_wav(frames: list[bytes], samplerate: int) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(b"".join(frames))
    return buf.getvalue()


def _write_tempfile(wav_bytes: bytes) -> Path:
    import tempfile

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.write(wav_bytes)
    tmp.close()
    return Path(tmp.name)


# ── Clipboard ─────────────────────────────────────────────────────────────────


def _write_clipboard(text: str) -> None:
    import shutil
    import subprocess

    for cmd in [
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ]:
        if shutil.which(cmd[0]):
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                proc.communicate(input=text.encode())
                if proc.returncode == 0:
                    return
            except Exception:
                pass
    print("[stt] clipboard write failed: no wl-copy/xclip/xsel found", file=sys.stderr)


# ── Chime wrapper ─────────────────────────────────────────────────────────────


def _chime(kind: str) -> None:
    from voicecli.samples import _chime as samples_chime

    samples_chime(kind)


# ── warmup (re-exported so tests can patch voicecli.stt_daemon.warmup) ────────


def warmup(model: str) -> None:
    from voicecli.transcribe import warmup as _warmup

    _warmup(model)


# ── Recording thread (pyaudio path) ──────────────────────────────────────────


class RecordingThread(threading.Thread):
    SAMPLERATE = 16000
    CHANNELS = 1
    CHUNK = 1024

    def __init__(self, level_callback=None):
        super().__init__(daemon=True)
        self.level_callback = level_callback
        self.frames: list[bytes] = []
        self._stop_event = threading.Event()

    def run(self) -> None:
        import pyaudio
        import numpy as np

        pa = pyaudio.PyAudio()
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=self.CHANNELS,
            rate=self.SAMPLERATE,
            input=True,
            frames_per_buffer=self.CHUNK,
        )
        while not self._stop_event.is_set():
            data = stream.read(self.CHUNK, exception_on_overflow=False)
            self.frames.append(data)
            if self.level_callback:
                level = np.abs(np.frombuffer(data, dtype=np.int16)).mean() / 32768.0
                self.level_callback(level)
        stream.stop_stream()
        stream.close()
        pa.terminate()

    def stop(self) -> bytes:
        self._stop_event.set()
        self.join(timeout=2.0)
        return _frames_to_wav(self.frames, self.SAMPLERATE)


# ── parecord fallback ─────────────────────────────────────────────────────────


def _record_parecord(stop_event: threading.Event) -> bytes:
    """Record via parecord subprocess until stop_event is set; return WAV bytes."""
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        proc = subprocess.Popen(
            [
                "parecord",
                "--format=s16le",
                "--rate=16000",
                "--channels=1",
                "--file-format=wav",
                str(tmp_path),
            ]
        )
        stop_event.wait()
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        return tmp_path.read_bytes() if tmp_path.exists() else b""
    finally:
        tmp_path.unlink(missing_ok=True)


# ── SttDaemon ────────────────────────────────────────────────────────────────


class SttDaemon:
    def __init__(self, model: str = "large-v3-turbo", socket_path: Path | None = None):
        self.model = model
        self._socket_path = Path(socket_path) if socket_path is not None else SOCKET_PATH
        self._state = State.IDLE
        self._lock = threading.Lock()
        self._recording_thread: RecordingThread | None = None
        self._use_pyaudio: bool = True  # set by _probe_pyaudio() in serve()
        self._parecord_stop_event: threading.Event | None = None
        # Used to shut down the accept loop from outside (tests / stop())
        self._server_socket: socket.socket | None = None

    # ── Public control ────────────────────────────────────────────────────────

    def stop(self) -> None:
        """Signal the accept loop to exit (used by tests)."""
        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except Exception:
                pass

    # ── Serve (accept loop) ───────────────────────────────────────────────────

    def serve(self) -> None:
        self._use_pyaudio = _probe_pyaudio()
        warmup(self.model)
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._socket_path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as srv:
            self._server_socket = srv
            srv.bind(str(self._socket_path))
            srv.listen(5)
            print(f"[voicecli stt] Ready on {self._socket_path}", flush=True)
            try:
                while True:
                    conn, _ = srv.accept()
                    threading.Thread(target=self._handle, args=(conn,), daemon=True).start()
            except (KeyboardInterrupt, OSError):
                # OSError is raised when _server_socket is closed by stop()
                pass
            finally:
                self._socket_path.unlink(missing_ok=True)

    # ── Request dispatch ──────────────────────────────────────────────────────

    def _handle(self, conn: socket.socket) -> None:
        try:
            req = _recv_json(conn)
            action = req.get("action")
            # mode field: parsed but ignored (reserved for issue #8)
            _ = req.get("mode")
            if action == "ping":
                self._handle_ping(conn)
            elif action == "status":
                self._handle_status(conn)
            elif action == "toggle":
                self._handle_toggle(conn)
            else:
                self._handle_unknown(conn, action)
        except Exception as exc:
            try:
                _send_json(conn, {"status": "error", "message": str(exc)})
            except Exception:
                pass
        finally:
            conn.close()

    def _handle_ping(self, conn: socket.socket) -> None:
        _send_json(conn, {"status": "ok"})

    def _handle_status(self, conn: socket.socket) -> None:
        with self._lock:
            state = self._state.value
        _send_json(conn, {"status": "ok", "state": state})

    def _handle_unknown(self, conn: socket.socket, action: str) -> None:
        _send_json(conn, {"status": "error", "message": f"unknown action: {action}"})

    def _handle_toggle(self, conn: socket.socket) -> None:
        with self._lock:
            state = self._state
        if state == State.IDLE:
            self._start_recording(conn)
        elif state == State.RECORDING:
            # _stop_and_transcribe blocks until transcription is done, then sends
            # the response via conn.  The caller (_handle) must NOT close conn
            # afterwards — _stop_and_transcribe takes ownership.
            self._stop_and_transcribe(conn)
        elif state == State.TRANSCRIBING:
            self._queue_recording(conn)
        elif state == State.QUEUED:
            _send_json(conn, {"status": "ok", "state": State.QUEUED.value})

    # ── State transitions ─────────────────────────────────────────────────────

    def _start_recording(self, conn: socket.socket) -> None:
        with self._lock:
            self._state = State.RECORDING
            if self._use_pyaudio:
                self._recording_thread = RecordingThread()
                self._parecord_stop_event = None
            else:
                self._recording_thread = None
                self._parecord_stop_event = threading.Event()
        if self._recording_thread:
            self._recording_thread.start()
        elif self._parecord_stop_event is not None:
            # Start parecord in a background thread; wav bytes collected on stop
            stop_ev = self._parecord_stop_event
            wav_holder: list[bytes] = []

            def _run_parecord():
                wav_holder.append(_record_parecord(stop_ev))

            t = threading.Thread(target=_run_parecord, daemon=True)
            t.start()
            # Store thread ref so _stop_and_transcribe can join it
            self._parecord_thread = t
            self._parecord_wav_holder = wav_holder
        threading.Thread(target=_chime, args=("start",), daemon=True).start()
        _send_json(conn, {"status": "ok", "state": State.RECORDING.value})

    def _stop_and_transcribe(self, conn: socket.socket) -> None:
        with self._lock:
            self._state = State.TRANSCRIBING
            rt = self._recording_thread
            self._recording_thread = None
            parecord_stop_ev = self._parecord_stop_event
            self._parecord_stop_event = None

        # Collect WAV bytes from whichever recording path was active
        if rt is not None:
            wav_bytes = rt.stop()
        elif parecord_stop_ev is not None:
            parecord_stop_ev.set()
            parecord_thread = getattr(self, "_parecord_thread", None)
            wav_holder = getattr(self, "_parecord_wav_holder", [])
            if parecord_thread is not None:
                parecord_thread.join(timeout=3.0)
            wav_bytes = wav_holder[0] if wav_holder else b""
        else:
            wav_bytes = b""

        tmp_path = _write_tempfile(wav_bytes)
        text: str = ""
        language: str | None = None
        try:
            from voicecli.transcribe import transcribe

            result = transcribe(tmp_path, model=self.model)
            text = result.text
            language = result.language
        except Exception as e:
            print(f"[stt] transcription error: {e}", file=sys.stderr)
        finally:
            tmp_path.unlink(missing_ok=True)

        _write_clipboard(text)

        with self._lock:
            queued = self._state == State.QUEUED
            self._state = State.IDLE

        threading.Thread(target=_chime, args=("stop",), daemon=True).start()
        _send_json(
            conn, {"status": "ok", "state": State.IDLE.value, "text": text, "language": language}
        )

        if queued:
            self._start_recording_async()

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
            else:
                self._recording_thread = None
                self._parecord_stop_event = threading.Event()
                stop_ev = self._parecord_stop_event
                wav_holder: list[bytes] = []

                def _run_parecord():
                    wav_holder.append(_record_parecord(stop_ev))

                t = threading.Thread(target=_run_parecord, daemon=True)
                t.start()
                self._parecord_thread = t
                self._parecord_wav_holder = wav_holder

        if self._recording_thread:
            self._recording_thread.start()
        threading.Thread(target=_chime, args=("start",), daemon=True).start()


# ── Wire protocol (copied from daemon.py) ─────────────────────────────────────


def _send_json(sock: socket.socket, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False) + "\n"
    sock.sendall(payload.encode())


def _recv_json(sock: socket.socket) -> dict:
    buf = bytearray()
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf.extend(chunk)
        if b"\n" in buf:
            break
    line = buf.split(b"\n")[0]
    return json.loads(line)
