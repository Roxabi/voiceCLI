"""Recording infrastructure for the STT daemon."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from io import BytesIO
from pathlib import Path


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


def _frames_to_wav(frames: list[bytes], samplerate: int) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(b"".join(frames))
    return buf.getvalue()


def _write_tempfile(wav_bytes: bytes) -> Path:
    fd, name = tempfile.mkstemp(suffix=".wav")
    try:
        os.write(fd, wav_bytes)
    finally:
        os.close(fd)
    return Path(name)


def _save_recording(wav_bytes: bytes, text: str, language: str | None) -> None:
    """Save WAV audio and transcript to STT/audio_in and STT/texts_out."""
    if not wav_bytes and not text:
        return
    from datetime import datetime

    from voicecli.core.config import VOICECLI_DIR

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    lang_tag = f"_{language}" if language else ""

    if wav_bytes:
        audio_dir = VOICECLI_DIR / "STT" / "audio_in"
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_path = audio_dir / f"dictate{lang_tag}_{ts}.wav"
        audio_path.write_bytes(wav_bytes)
        print(f"[stt] saved audio: {audio_path}", file=sys.stderr)

    if text:
        text_dir = VOICECLI_DIR / "STT" / "texts_out"
        text_dir.mkdir(parents=True, exist_ok=True)
        text_path = text_dir / f"dictate{lang_tag}_{ts}.txt"
        text_path.write_text(text, encoding="utf-8")
        print(f"[stt] saved transcript: {text_path}", file=sys.stderr)


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


def _record_parecord(stop_event: threading.Event, level_callback=None) -> bytes:
    """Record via PulseAudio until stop_event is set; return WAV bytes.

    Prefers `parec` (raw PCM to stdout) which enables real-time level callbacks.
    Falls back to `parecord` (WAV to temp file, no levels) if parec is absent.
    """
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
