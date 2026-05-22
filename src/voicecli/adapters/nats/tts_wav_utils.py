"""WAV utility helpers for the TTS NATS adapter.

Extracted from tts_adapter to keep that module under the 300-line gate.
All functions are pure (no NATS / asyncio dependencies).
"""

from __future__ import annotations

import base64
import contextlib
import logging
import struct
import wave
from pathlib import Path

log = logging.getLogger(__name__)


def collect_chunked_output(out_path: Path) -> list[Path]:
    """Return sorted chunk paths if a .done marker exists, else empty list.

    When api.generate() runs in chunked mode it writes:
        {stem}_001.wav, {stem}_002.wav, … {stem}_NNN.wav
        {stem}.done  (sentinel written after all chunks)

    The adapter expects a single file at *out_path* ({stem}.wav). If the
    engine wrote chunks instead, this function returns them in order so the
    caller can concatenate them.
    """
    done_path = out_path.with_suffix(".done")
    if not done_path.exists():
        return []
    stem = out_path.stem
    parent = out_path.parent
    chunks = sorted(parent.glob(f"{stem}_*.wav"))
    return chunks


def concat_wav_chunks(chunks: list[Path], out_path: Path) -> None:
    """Concatenate WAV chunk files into *out_path* using the stdlib wave module.

    All chunks must share the same format (channels, sample width, frame rate).
    Raises ValueError if the chunk list is empty or format is inconsistent.
    """
    if not chunks:
        raise ValueError("concat_wav_chunks: chunk list is empty")

    with wave.open(str(chunks[0]), "rb") as first:
        params = first.getparams()

    with wave.open(str(out_path), "wb") as out_wav:
        out_wav.setparams(params)
        for chunk_path in chunks:
            with wave.open(str(chunk_path), "rb") as chunk_wav:
                if (
                    chunk_wav.getnchannels() != params.nchannels
                    or chunk_wav.getsampwidth() != params.sampwidth
                    or chunk_wav.getframerate() != params.framerate
                ):
                    raise ValueError(
                        f"chunk format mismatch in {chunk_path}: "
                        f"expected {params.nchannels}ch/{params.sampwidth}sw/{params.framerate}Hz"
                    )
                out_wav.writeframes(chunk_wav.readframes(chunk_wav.getnframes()))


def cleanup_chunks(out_path: Path, chunks: list[Path]) -> None:
    """Remove chunk files and the .done sentinel. Idempotent."""
    done_path = out_path.with_suffix(".done")
    for p in [*chunks, done_path]:
        with contextlib.suppress(FileNotFoundError):
            p.unlink()


def wav_duration_ms(path: Path) -> int:
    """Read WAV header to compute duration in milliseconds. Returns 0 on failure."""
    try:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate > 0:
                return int(frames / rate * 1000)
    except Exception:
        pass
    return 0


def wav_waveform_b64(path: Path, num_samples: int = 256) -> str | None:
    """Compute a 256-byte amplitude waveform from a WAV file.

    Mirrors lyra's `_wav_waveform_b64` so Discord voice-message waveforms
    can be rendered without a second decoding pass hub-side.
    Returns None on any error (field is optional in ADR-044).
    """
    try:
        with wave.open(str(path), "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)

        if sampwidth == 1:
            samples = [raw[i] - 128 for i in range(0, len(raw), n_channels)]
            max_val = 128
        elif sampwidth == 2:
            samples = [
                struct.unpack_from("<h", raw, i)[0] for i in range(0, len(raw) - 1, 2 * n_channels)
            ]
            max_val = 32768
        else:
            return None

        if not samples:
            return None

        chunk = max(1, len(samples) // num_samples)
        waveform = bytearray()
        for i in range(num_samples):
            sl = samples[i * chunk : i * chunk + chunk]
            amp = sum(abs(x) for x in sl) // len(sl) if sl else 0
            waveform.append(min(255, int(amp * 255 / max_val)))
        return base64.b64encode(bytes(waveform)).decode("ascii")
    except Exception:
        log.warning("waveform_b64 computation failed", exc_info=True)
        return None
