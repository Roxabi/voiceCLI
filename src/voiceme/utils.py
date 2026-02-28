from datetime import datetime
from pathlib import Path

OUTPUT_DIR = Path("TTS/voices_out")

# Map full language names to ISO 639-1 codes (shared across engines and utils)
LANG_MAP = {
    "arabic": "ar", "danish": "da", "german": "de", "greek": "el",
    "english": "en", "spanish": "es", "finnish": "fi", "french": "fr",
    "hebrew": "he", "hindi": "hi", "italian": "it", "japanese": "ja",
    "korean": "ko", "malay": "ms", "dutch": "nl", "norwegian": "no",
    "polish": "pl", "portuguese": "pt", "russian": "ru", "swedish": "sv",
    "swahili": "sw", "turkish": "tr", "chinese": "zh",
}


def resolve_language(language: str) -> str:
    """Convert a language name or code to an ISO 639-1 code."""
    lang = language.lower().strip()
    if lang in LANG_MAP.values():
        return lang
    if lang in LANG_MAP:
        return LANG_MAP[lang]
    return "en"


def default_output_path(prefix: str = "voiceme", fmt: str = "wav") -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return OUTPUT_DIR / f"{prefix}_{ts}.{fmt}"


def build_output_prefix(
    engine: str,
    *,
    script: str | None = None,
    voice: str | None = None,
    language: str | None = None,
    clone: bool = False,
) -> str:
    """Build a descriptive output filename prefix.

    Examples:
        futur_ia_qwen_ono_anna_fr
        darwinisme_chatterbox-turbo_clone_en
        qwen_serena_fr
    """
    parts: list[str] = []
    if script:
        parts.append(script)
    parts.append(engine)
    if clone:
        parts.append("clone")
    if voice and voice != "default":
        parts.append(voice.lower())
    if language:
        parts.append(resolve_language(language))
    return "_".join(parts)


def split_sentences(text: str, max_chars: int = 250) -> list[str]:
    """Split text into sentence-sized chunks (avoids Chatterbox 40s cutoff)."""
    import re

    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) <= max_chars:
            current += (" " + s if current else s)
        else:
            if current:
                chunks.append(current.strip())
            current = s
    if current:
        chunks.append(current.strip())
    return chunks


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
