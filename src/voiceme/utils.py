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


def split_sentences(text: str) -> list[str]:
    """Split text into individual sentences (avoids Chatterbox 40s cutoff)."""
    import re

    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in sentences if s.strip()]


def smart_chunk(text: str, target_chars: int = 500) -> list[str]:
    """Split text into chunks of ~target_chars at natural boundaries.

    Hierarchy: paragraphs > sentences > target_chars.
    Each chunk is between 1 and ~1.5x target_chars.
    """
    import re

    paragraphs = re.split(r"\n\n+", text.strip())
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        if current and len(current) + len(para) + 2 <= target_chars:
            current += "\n\n" + para
        else:
            if current:
                chunks.append(current.strip())
            if len(para) > target_chars:
                # Split long paragraph by sentences
                sentences = split_sentences(para)
                sub = ""
                for sent in sentences:
                    if sub and len(sub) + len(sent) + 1 <= target_chars:
                        sub += " " + sent
                    else:
                        if sub:
                            chunks.append(sub.strip())
                        sub = sent
                current = sub
            else:
                current = para

    if current:
        chunks.append(current.strip())

    return chunks if chunks else [text]


def concat_audio(
    chunks: list,
    sample_rate: int,
    gaps_ms: list[int] | None = None,
    crossfades_ms: list[int] | None = None,
):
    """Concatenate audio chunks with per-pair silence gaps and/or crossfades.

    Args:
        chunks: list of 1-D numpy arrays (mono audio).
        sample_rate: samples per second.
        gaps_ms: silence duration (ms) between each pair. Length = len(chunks)-1.
        crossfades_ms: fade duration (ms) between each pair. Length = len(chunks)-1.

    The 4 combinations per pair:
        gap=0, xfade=0  → direct concat
        gap>0, xfade=0  → hard cut | silence | hard cut
        gap=0, xfade>0  → fade-out then fade-in (no silence)
        gap>0, xfade>0  → fade-out | silence | fade-in
    """
    import numpy as np

    if len(chunks) == 0:
        return np.array([], dtype=np.float32)
    if len(chunks) == 1:
        return chunks[0]

    n_pairs = len(chunks) - 1
    gaps = gaps_ms or [0] * n_pairs
    xfades = crossfades_ms or [0] * n_pairs

    # Fast path: no gaps or crossfades at all
    if all(g == 0 for g in gaps) and all(x == 0 for x in xfades):
        return np.concatenate(chunks)

    parts: list[np.ndarray] = []
    for i, chunk in enumerate(chunks):
        chunk = chunk.astype(np.float32, copy=True)

        # Apply fade-out to tail of this chunk (for transition to next)
        if i < n_pairs and xfades[i] > 0:
            xf_samples = min(int(sample_rate * xfades[i] / 1000), len(chunk))
            if xf_samples > 0:
                fade_out = np.linspace(1.0, 0.0, xf_samples, dtype=np.float32)
                chunk[-xf_samples:] *= fade_out

        # Apply fade-in to head of this chunk (for transition from previous)
        if i > 0 and xfades[i - 1] > 0:
            xf_samples = min(int(sample_rate * xfades[i - 1] / 1000), len(chunk))
            if xf_samples > 0:
                fade_in = np.linspace(0.0, 1.0, xf_samples, dtype=np.float32)
                chunk[:xf_samples] *= fade_in

        parts.append(chunk)

        # Insert silence gap after this chunk (before next)
        if i < n_pairs and gaps[i] > 0:
            silence = np.zeros(int(sample_rate * gaps[i] / 1000), dtype=np.float32)
            parts.append(silence)

    return np.concatenate(parts)


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
