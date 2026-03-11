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
import os
import socket
import struct
import sys
import threading
from enum import Enum
from pathlib import Path

SOCKET_PATH = Path.home() / ".local" / "share" / "voicecli" / "stt-daemon.sock"
HISTORY_PATH = Path.home() / ".local" / "share" / "voicecli" / "stt-history.jsonl"
HISTORY_MAX = 100

MAX_MSG = 65536

LEVEL_FILE = Path("/tmp/voicecli_audio_level")


# ── State machine ─────────────────────────────────────────────────────────────


class State(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    QUEUED = "queued"


# ── pyaudio probe ─────────────────────────────────────────────────────────────


def _is_wsl() -> bool:
    return "WSL_DISTRO_NAME" in os.environ or (
        Path("/proc/version").exists() and "microsoft" in Path("/proc/version").read_text().lower()
    )


def _probe_pyaudio() -> bool:
    """Return True if pyaudio is usable; print warning and return False otherwise."""
    try:
        import pyaudio

        # Suppress ALSA/JACK noise that pyaudio prints unconditionally on startup
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        saved_stderr = os.dup(2)
        os.dup2(devnull_fd, 2)
        try:
            pa = pyaudio.PyAudio()
            try:
                stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=16000,
                    input=True,
                    frames_per_buffer=1024,
                )
                stream.close()
            finally:
                pa.terminate()
        finally:
            os.dup2(saved_stderr, 2)
            os.close(saved_stderr)
            os.close(devnull_fd)
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
    import os
    import tempfile

    fd, name = tempfile.mkstemp(suffix=".wav")
    try:
        os.write(fd, wav_bytes)
    finally:
        os.close(fd)
    return Path(name)


# ── Clipboard ─────────────────────────────────────────────────────────────────


def _write_clipboard(text: str) -> None:
    import shutil
    import subprocess

    for cmd in [
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["clip.exe"],
    ]:
        if shutil.which(cmd[0]):
            try:
                encoding = "utf-16-le" if cmd[0] == "clip.exe" else "utf-8"
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                proc.communicate(input=text.encode(encoding))
                if proc.returncode == 0:
                    return
            except Exception:
                pass
    # Build a helpful install suggestion based on environment
    if _is_wsl():
        suggestion = (
            "clip.exe is built-in on WSL2 — check WSL_INTEROP is set, or: sudo apt install xclip"
        )
    elif shutil.which("apt"):
        suggestion = "sudo apt install wl-clipboard"
    elif shutil.which("dnf"):
        suggestion = "sudo dnf install wl-clipboard"
    elif shutil.which("pacman"):
        suggestion = "sudo pacman -S wl-clipboard"
    else:
        suggestion = "install wl-clipboard or xclip"
    print(
        f"[stt] clipboard write failed: no wl-copy/xclip/xsel/clip.exe found — {suggestion}",
        file=sys.stderr,
    )


# ── Auto-paste ────────────────────────────────────────────────────────────────


def _auto_paste() -> None:
    """Trigger a Ctrl+Shift+V paste (no formatting) in the active window.

    On WSL2: writes a flag file that the AHK script polls every 150 ms.
    AHK then sends ^+v natively on the Windows side — no powershell startup lag,
    no foreground-window race conditions.

    Fallback: xdotool (native Linux / WSLg X11 windows).
    """
    import subprocess
    import time

    time.sleep(0.15)  # small grace period so overlay close is processed first

    if _is_wsl():
        # Resolve Windows %TEMP% → WSL path and drop the trigger file
        try:
            r = subprocess.run(
                ["cmd.exe", "/c", "echo %TEMP%"],
                capture_output=True,
                timeout=3,
            )
            win_path = r.stdout.decode(
                "cp850", errors="replace"
            ).strip()  # e.g. C:\Users\Mickael\AppData\Local\Temp
            if len(win_path) >= 3 and win_path[1] == ":":
                drive = win_path[0].lower()
                rest = win_path[2:].replace("\\", "/")
                trigger = Path(f"/mnt/{drive}{rest}/voicecli_paste_trigger")
                trigger.write_text("1")
                print("[stt] auto-paste: trigger written for AHK", file=sys.stderr)
                return
        except Exception as e:
            print(f"[stt] auto-paste AHK trigger failed: {e}", file=sys.stderr)

    # Fallback: xdotool (native Linux or WSLg X11)
    import shutil

    if shutil.which("xdotool"):
        try:
            subprocess.Popen(
                ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except Exception as e:
            print(f"[stt] auto-paste xdotool failed: {e}", file=sys.stderr)

    print("[stt] auto-paste: no suitable method (need AHK trigger or xdotool)", file=sys.stderr)


# ── Recording saver ───────────────────────────────────────────────────────────


def _save_recording(wav_bytes: bytes, text: str, language: str | None) -> None:
    """Save WAV audio and transcript to STT/audio_in and STT/texts_out."""
    if not wav_bytes and not text:
        return
    from datetime import datetime
    from voicecli.config import _find_config

    # Derive project root from voicecli.toml location, fall back to cwd
    cfg_path = _find_config()
    project_root = cfg_path.parent if cfg_path else Path.cwd()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    lang_tag = f"_{language}" if language else ""

    if wav_bytes:
        audio_dir = project_root / "STT" / "audio_in"
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_path = audio_dir / f"dictate{lang_tag}_{ts}.wav"
        audio_path.write_bytes(wav_bytes)
        print(f"[stt] saved audio: {audio_path}", file=sys.stderr)

    if text:
        text_dir = project_root / "STT" / "texts_out"
        text_dir.mkdir(parents=True, exist_ok=True)
        text_path = text_dir / f"dictate{lang_tag}_{ts}.txt"
        text_path.write_text(text, encoding="utf-8")
        print(f"[stt] saved transcript: {text_path}", file=sys.stderr)


# ── History ───────────────────────────────────────────────────────────────────


def _wav_duration_s(wav_bytes: bytes) -> float | None:
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


def _append_history(
    text: str,
    language: str | None,
    mode: str | None,
    duration_s: float | None,
) -> None:
    """Append one entry to the JSONL history file, capping at HISTORY_MAX entries."""
    import json as _json
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
            f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
        # Trim only when over cap (read-modify-write is rare)
        lines = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
        if len(lines) > HISTORY_MAX:
            HISTORY_PATH.write_text("\n".join(lines[-HISTORY_MAX:]) + "\n", encoding="utf-8")
    except Exception as e:
        print(f"[stt] history write failed: {e}", file=sys.stderr)


# ── Overlay launcher ──────────────────────────────────────────────────────────


def _spawn_overlay(mode: str | None = None) -> None:
    """Launch the waveform overlay from the daemon process (survives WSL session exit)."""
    import subprocess
    import sys

    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    if mode:
        env["VOICECLI_OVERLAY_MODE"] = mode
    log = Path(os.environ.get("TMPDIR", "/tmp")) / "voicecli_overlay.log"
    try:
        subprocess.Popen(
            [sys.executable, "-m", "voicecli.overlay"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=open(log, "w"),
            env=env,
        )
    except Exception as e:
        print(f"[stt] overlay spawn failed: {e}", file=sys.stderr)


# ── UI sound ──────────────────────────────────────────────────────────────────


def _play_ui_sound(name: str) -> None:
    """Play a UI sound from the assets directory via paplay (non-blocking)."""
    import subprocess
    import sys

    assets = (
        Path(sys.executable).parent.parent
        / "lib"
        / "python3.12"
        / "site-packages"
        / "voicecli"
        / "assets"
    )
    # Fallback: resolve relative to this file
    assets_local = Path(__file__).parent / "assets"
    path = assets_local / name if assets_local.exists() else assets / name
    if path.exists():
        subprocess.Popen(
            ["paplay", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )


# ── Chime wrapper ─────────────────────────────────────────────────────────────


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
        import numpy as np
        import pyaudio

        # Suppress ALSA/JACK chatter on stream open
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        saved_stderr = os.dup(2)
        os.dup2(devnull_fd, 2)
        try:
            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=self.CHANNELS,
                rate=self.SAMPLERATE,
                input=True,
                frames_per_buffer=self.CHUNK,
            )
        finally:
            os.dup2(saved_stderr, 2)
            os.close(saved_stderr)
            os.close(devnull_fd)

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
        if self.is_alive():
            print(
                "[stt] WARNING: RecordingThread did not stop in 2s — audio may be truncated",
                file=sys.stderr,
                flush=True,
            )
        return _frames_to_wav(self.frames, self.SAMPLERATE)


# ── parecord fallback ─────────────────────────────────────────────────────────


def _record_parecord(stop_event: threading.Event, level_callback=None) -> bytes:
    """Record via PulseAudio until stop_event is set; return WAV bytes.

    Prefers `parec` (raw PCM to stdout) which enables real-time level callbacks.
    Falls back to `parecord` (WAV to temp file, no levels) if parec is absent.
    """
    import shutil
    import subprocess
    import tempfile

    SAMPLERATE = 16000
    CHUNK = 3200  # ~100 ms at 16 kHz 16-bit mono

    parec = shutil.which("parec")
    if parec:
        # parec writes raw s16le to stdout — ideal for real-time processing
        proc = subprocess.Popen(
            [parec, "--format=s16le", f"--rate={SAMPLERATE}", "--channels=1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        frames: list[bytes] = []

        def _reader() -> None:
            import numpy as np

            assert proc.stdout is not None
            while True:
                data = proc.stdout.read(CHUNK)
                if not data:
                    break
                frames.append(data)
                if level_callback and len(data) >= 2:
                    samps = np.frombuffer(data, dtype=np.int16)
                    level_callback(float(np.sqrt(np.mean(samps.astype(np.float32) ** 2))) / 32768.0)

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()
        stop_event.wait()
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        reader.join(timeout=2.0)
        return _frames_to_wav(frames, SAMPLERATE)

    # Fallback: parecord writes WAV to a temp file (no real-time level data)
    parecord = shutil.which("parecord")
    if not parecord:
        return b""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_path = Path(f.name)
    try:
        proc2 = subprocess.Popen(
            [
                parecord,
                "--format=s16le",
                f"--rate={SAMPLERATE}",
                "--channels=1",
                "--file-format=wav",
                str(tmp_path),
            ]
        )
        stop_event.wait()
        proc2.terminate()
        try:
            proc2.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc2.kill()
        return tmp_path.read_bytes() if tmp_path.exists() else b""
    finally:
        tmp_path.unlink(missing_ok=True)


# ── SttDaemon ────────────────────────────────────────────────────────────────


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
            req = _recv_json(conn)
            action = req.get("action")
            mode = req.get("mode") or None
            if action == "ping":
                self._handle_ping(conn)
            elif action == "status":
                self._handle_status(conn)
            elif action == "toggle":
                self._handle_toggle(conn, mode=mode)
            elif action == "cancel":
                self._handle_cancel(conn)
            elif action == "next_mode":
                self._handle_next_mode(conn)
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
            mode = self._current_mode or self.default_mode
        _send_json(conn, {"status": "ok", "state": state, "mode": mode})

    def _handle_unknown(self, conn: socket.socket, action: str) -> None:
        _send_json(conn, {"status": "error", "message": f"unknown action: {action}"})

    def _handle_toggle(self, conn: socket.socket, mode: str | None = None) -> None:
        with self._lock:
            state = self._state
        if state == State.IDLE:
            self._start_recording(conn, mode=mode)
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
        threading.Thread(target=_play_ui_sound, args=("start.wav",), daemon=True).start()
        threading.Thread(target=_spawn_overlay, args=(effective_mode,), daemon=True).start()
        _send_json(conn, {"status": "ok", "state": State.RECORDING.value})

    def _stop_and_transcribe(self, conn: socket.socket) -> None:
        with self._lock:
            self._state = State.TRANSCRIBING
            rt = self._recording_thread
            self._recording_thread = None
            parecord_stop_ev = self._parecord_stop_event
            self._parecord_stop_event = None
            current_mode = self._current_mode
            self._current_mode = None

        # Collect WAV bytes from whichever recording path was active
        if rt is not None:
            wav_bytes = rt.stop()
        elif parecord_stop_ev is not None:
            parecord_stop_ev.set()
            parecord_thread = self._parecord_thread
            wav_holder = self._parecord_wav_holder
            if parecord_thread is not None:
                parecord_thread.join(timeout=3.0)
            wav_bytes = wav_holder[0] if wav_holder else b""
        else:
            wav_bytes = b""

        # Resolve mode params (mode overrides daemon-level settings)
        transcribe_language = self.language
        transcribe_task = "transcribe"
        transcribe_prompt: str | None = None
        if current_mode is not None:
            try:
                from voicecli.config import load_config
                from voicecli.stt_modes import get_mode

                mode_cfg = get_mode(current_mode, load_config())
                if "language" in mode_cfg:
                    transcribe_language = mode_cfg["language"]
                if "task" in mode_cfg:
                    transcribe_task = mode_cfg["task"]
                if "prompt" in mode_cfg:
                    transcribe_prompt = mode_cfg["prompt"]
            except Exception as e:
                print(f"[stt] mode resolve error: {e}", file=sys.stderr)

        tmp_path = _write_tempfile(wav_bytes)
        text: str = ""
        language: str | None = None
        try:
            from voicecli.transcribe import transcribe

            result = transcribe(
                tmp_path,
                model=self.model,
                language=transcribe_language,
                language_detection_threshold=self.language_detection_threshold,
                language_detection_segments=self.language_detection_segments,
                language_fallback=self.language_fallback,
                task=transcribe_task,
                initial_prompt=transcribe_prompt,
            )
            text = result.text
            language = result.language
            print(f"[stt] detected language: {language}", file=sys.stderr)
        except Exception as e:
            print(f"[stt] transcription error: {e}", file=sys.stderr)
        finally:
            tmp_path.unlink(missing_ok=True)

        try:
            _write_clipboard(text)
        except Exception as e:
            print(f"[stt] clipboard error: {e}", file=sys.stderr)

        if text and self.auto_paste:
            threading.Thread(target=_auto_paste, daemon=True).start()

        _save_recording(wav_bytes, text, language)

        duration_s = _wav_duration_s(wav_bytes)
        _append_history(text, language, current_mode, duration_s)

        with self._lock:
            queued = self._state == State.QUEUED
            self._state = State.IDLE

        _send_json(
            conn, {"status": "ok", "state": State.IDLE.value, "text": text, "language": language}
        )

        if queued:
            self._start_recording_async()

    def _handle_cancel(self, conn: socket.socket) -> None:
        with self._lock:
            state = self._state
            if state not in (State.RECORDING, State.QUEUED):
                _send_json(conn, {"status": "ok", "state": State.IDLE.value})
                return
            self._state = State.IDLE
            rt = self._recording_thread
            self._recording_thread = None
            stop_ev = self._parecord_stop_event
            self._parecord_stop_event = None

        if rt is not None:
            threading.Thread(target=rt.stop, daemon=True).start()
        if stop_ev is not None:
            stop_ev.set()
        _send_json(conn, {"status": "ok", "state": State.IDLE.value})

    def _handle_next_mode(self, conn: socket.socket) -> None:
        """Cycle to the next available mode and update default_mode."""
        from voicecli.config import _find_config
        from voicecli.stt_modes import load_modes

        cfg_path = _find_config()
        raw_cfg: dict = {}
        if cfg_path:
            import tomllib

            with open(cfg_path, "rb") as f:
                raw_cfg = tomllib.load(f)
        modes = load_modes(raw_cfg)
        mode_names = sorted(modes.keys())
        current = self._current_mode or self.default_mode
        if current in mode_names:
            idx = (mode_names.index(current) + 1) % len(mode_names)
        else:
            idx = 0
        next_mode = mode_names[idx]
        self.default_mode = next_mode
        self._current_mode = next_mode if self._state == State.RECORDING else None
        desc = modes[next_mode].get("description", next_mode)
        _send_json(conn, {"status": "ok", "mode": next_mode, "description": desc})

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


# ── Wire protocol (copied from daemon.py) ─────────────────────────────────────


def _send_json(sock: socket.socket, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False) + "\n"
    sock.sendall(payload.encode())


def _recv_json(sock: socket.socket) -> dict:
    buf = bytearray()
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf.extend(chunk)
        if b"\n" in buf or len(buf) >= MAX_MSG:
            break
    line = buf.split(b"\n")[0]
    return json.loads(line)
