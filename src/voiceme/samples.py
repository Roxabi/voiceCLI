"""Sample management: list, add, record, use, active, remove."""

import shutil
from pathlib import Path

SAMPLES_DIR = Path("TTS/samples")
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


def _play_wav(samples: "np.ndarray", samplerate: int = 44100) -> None:
    """Play a numpy int16 array via paplay."""
    import struct
    import subprocess
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
        # Warm rising triad: C4 → E4 → G4 → C5, each gently staggered
        duration = 1.8
        t = np.linspace(0, duration, int(samplerate * duration), endpoint=False)
        signal = (
            _note(t, 262, onset=0.0, sustain=0.4, release=0.8, volume=0.15)    # C4
            + _note(t, 330, onset=0.25, sustain=0.4, release=0.7, volume=0.18)  # E4
            + _note(t, 392, onset=0.50, sustain=0.4, release=0.6, volume=0.20)  # G4
            + _note(t, 523, onset=0.75, sustain=0.5, release=0.5, volume=0.22)  # C5
        )
        # Global smooth fade-in and fade-out
        signal *= np.clip(t / 0.15, 0, 1) * np.clip((duration - t) / 0.4, 0, 1)
    else:
        # Soft resolved closure: G4 → E4 with gentle decay
        duration = 1.0
        t = np.linspace(0, duration, int(samplerate * duration), endpoint=False)
        signal = (
            _note(t, 392, onset=0.0, sustain=0.3, release=0.5, volume=0.18)   # G4
            + _note(t, 262, onset=0.25, sustain=0.4, release=0.4, volume=0.15)  # C4
        )
        signal *= np.clip((duration - t) / 0.3, 0, 1)

    signal = np.clip(signal, -1, 1)
    signal = (signal * 32767).astype(np.int16)
    _play_wav(signal, samplerate)


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
