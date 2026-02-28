from datetime import datetime
from pathlib import Path

OUTPUT_DIR = Path("output")


def default_output_path(prefix: str = "voiceme", fmt: str = "wav") -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return OUTPUT_DIR / f"{prefix}_{ts}.{fmt}"


def wav_to_mp3(wav_path: Path, bitrate: int = 192) -> Path:
    """Convert a WAV file to MP3 using lameenc. Returns the MP3 path."""
    import lameenc
    import soundfile as sf

    audio, sr = sf.read(wav_path, dtype="int16")

    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bitrate)
    encoder.set_in_sample_rate(sr)
    encoder.set_channels(1 if audio.ndim == 1 else audio.shape[1])
    encoder.set_quality(2)

    mp3_data = encoder.encode(audio.tobytes())
    mp3_data += encoder.flush()

    mp3_path = wav_path.with_suffix(".mp3")
    mp3_path.write_bytes(mp3_data)
    return mp3_path
