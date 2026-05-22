"""Sample management: list, add, record, use, active, remove, from-url."""

import shutil
import subprocess
from pathlib import Path

SAMPLES_DIR = Path.home() / ".voicecli" / "TTS" / "samples"
ACTIVE_FILE = SAMPLES_DIR / ".active"


def ensure_dir() -> Path:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    return SAMPLES_DIR


def list_samples() -> list[str]:
    ensure_dir()
    return sorted(p.name for p in SAMPLES_DIR.glob("*.wav"))


def add_sample(source: Path) -> Path:
    ensure_dir()
    if not source.exists():
        raise FileNotFoundError(f"File not found: {source}")
    dest = SAMPLES_DIR / source.name
    shutil.copy2(source, dest)
    return dest


def remove_sample(name: str) -> None:
    path = SAMPLES_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Sample not found: {name}")
    path.unlink()
    # Clear active if it was pointing to this sample
    if get_active() == name:
        ACTIVE_FILE.unlink(missing_ok=True)


def set_active(name: str) -> None:
    path = SAMPLES_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Sample not found: {name}")
    ACTIVE_FILE.write_text(name)


def get_active() -> str | None:
    if ACTIVE_FILE.exists():
        name = ACTIVE_FILE.read_text().strip()
        if name and (SAMPLES_DIR / name).exists():
            return name
    return None


def get_active_path() -> Path | None:
    name = get_active()
    if name:
        return SAMPLES_DIR / name
    return None


def _play_wav(samples, samplerate: int = 44100) -> None:
    """Play a numpy int16 array via paplay."""
    import struct
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        data_size = len(samples) * 2
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_size))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, 1, samplerate, samplerate * 2, 2, 16))
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(samples.tobytes())
        tmp_path = f.name

    try:
        subprocess.run(["paplay", tmp_path], check=True)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _chime(kind: str = "start", samplerate: int = 44100) -> None:
    """Play a gentle chime. 'start' = warm rising invitation, 'stop' = soft resolved closure."""
    import numpy as np

    def _note(t, freq, onset, sustain, release, volume=0.2):
        """Single note with exponential attack/decay and harmonics for warmth."""
        env = np.zeros_like(t)
        active = t >= onset
        elapsed = t[active] - onset
        # Smooth attack (exponential rise)
        attack_mask = elapsed < sustain
        env_active = np.zeros(elapsed.shape)
        env_active[attack_mask] = 1.0 - np.exp(-elapsed[attack_mask] / 0.06)
        # Exponential decay after sustain
        decay_mask = ~attack_mask
        env_active[decay_mask] = (1.0 - np.exp(-sustain / 0.06)) * np.exp(
            -(elapsed[decay_mask] - sustain) / release
        )
        env[active] = env_active

        # Fundamental + soft harmonics for a bell-like warmth
        wave = np.sin(2 * np.pi * freq * t)
        wave += 0.3 * np.sin(2 * np.pi * freq * 2 * t)  # octave
        wave += 0.1 * np.sin(2 * np.pi * freq * 3 * t)  # 5th above octave
        return volume * env * wave

    if kind == "start":
        # Bright major chord: C5 + E5 + G5, instant attack, bell decay ~350ms
        duration = 0.35
        t = np.linspace(0, duration, int(samplerate * duration), endpoint=False)
        signal = (
            _note(t, 523, onset=0.0, sustain=0.02, release=0.15, volume=0.20)  # C5
            + _note(t, 659, onset=0.0, sustain=0.02, release=0.13, volume=0.18)  # E5
            + _note(t, 784, onset=0.0, sustain=0.02, release=0.11, volume=0.16)  # G5
        )
    else:
        # Soft descending resolution: G4 → E4, ~300ms
        duration = 0.30
        t = np.linspace(0, duration, int(samplerate * duration), endpoint=False)
        signal = (
            _note(t, 392, onset=0.0, sustain=0.02, release=0.12, volume=0.18)  # G4
            + _note(t, 330, onset=0.06, sustain=0.02, release=0.12, volume=0.16)  # E4
        )

    signal = np.clip(signal, -1, 1)
    signal = (signal * 32767).astype(np.int16)
    _play_wav(signal, samplerate)


def _check_tool(name: str) -> None:
    """Raise RuntimeError if an external tool is not installed."""
    if not shutil.which(name):
        hints = {
            "yt-dlp": "Install with: uv tool install yt-dlp",
            "ffmpeg": "Install with: sudo apt install ffmpeg",
        }
        hint = hints.get(name, f"Please install {name}")
        raise RuntimeError(f"'{name}' not found on PATH. {hint}")


def from_url(
    url: str,
    name: str,
    *,
    start: float = 10.0,
    duration: float = 30.0,
) -> Path:
    """Download audio from a URL (YouTube etc.) via yt-dlp, extract and normalize a segment."""
    import tempfile
    from urllib.parse import urlparse

    if start < 0:
        raise ValueError(f"start must be non-negative, got {start}")
    if duration <= 0:
        raise ValueError(f"duration must be positive, got {duration}")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http/https URLs are supported, got '{parsed.scheme}://'")

    _check_tool("yt-dlp")
    _check_tool("ffmpeg")

    ensure_dir()
    # Sanitize name to a bare filename (prevent path traversal)
    name = Path(name).name
    if not name.endswith(".wav"):
        name = f"{name}.wav"
    dest = SAMPLES_DIR / name

    with tempfile.TemporaryDirectory() as tmpdir:
        raw_audio = Path(tmpdir) / "raw.%(ext)s"
        # Download best audio
        print(f"Downloading audio from {url}...")
        try:
            subprocess.run(
                [
                    "yt-dlp",
                    "--no-config",
                    "-x",
                    "--audio-format",
                    "wav",
                    "-o",
                    str(raw_audio),
                    "--",
                    url,
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"yt-dlp failed (exit {e.returncode}). Check the URL.") from e

        # Find the downloaded .wav file (yt-dlp replaces %(ext)s)
        downloaded = [p for p in Path(tmpdir).glob("raw.*") if p.suffix == ".wav"]
        if not downloaded:
            raise RuntimeError("yt-dlp did not produce a WAV output file")
        raw_file = downloaded[0]

        # Extract segment + normalize to mono 24kHz with loudnorm
        print(f"Extracting {duration}s segment from {start}s, normalizing...")
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(start),
                    "-t",
                    str(duration),
                    "-i",
                    str(raw_file),
                    "-ac",
                    "1",
                    "-ar",
                    "24000",
                    "-af",
                    "loudnorm=I=-16:TP=-1.5:LRA=11",
                    str(dest),
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"ffmpeg failed (exit {e.returncode}). The audio may be corrupted."
            ) from e

    print(f"Saved sample to {dest}")
    return dest


def record_sample(name: str, duration: float = 10.0, samplerate: int = 24000) -> Path:
    """Record from microphone via PulseAudio (parecord) and save as WAV."""
    import subprocess

    ensure_dir()
    if not name.endswith(".wav"):
        name = f"{name}.wav"
    dest = SAMPLES_DIR / name

    _chime("start")
    print(f"Recording for {duration}s...")

    try:
        subprocess.run(
            [
                "parecord",
                "--channels=1",
                f"--rate={samplerate}",
                "--format=s16le",
                "--file-format=wav",
                str(dest),
            ],
            timeout=duration,
        )
    except subprocess.TimeoutExpired:
        pass  # expected — this is how we stop after the set duration

    _chime("stop")
    print(f"Saved recording to {dest}")
    return dest
