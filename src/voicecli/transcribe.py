"""File-based speech-to-text using Faster Whisper."""

from dataclasses import dataclass
from pathlib import Path

MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
DEFAULT_MODEL = "large-v3-turbo"

_model_cache: dict[str, object] = {}


@dataclass
class TranscriptionResult:
    text: str
    language: str | None  # detected language code ("en", "fr", ...)
    segments: list[dict]  # [{start, end, text}, ...]


def transcribe(
    audio_path: Path, model: str = DEFAULT_MODEL, language: str | None = None
) -> TranscriptionResult:
    whisper = _load_model(model)
    segments, info = whisper.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
        vad_filter=True,
    )
    seg_list = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]
    full_text = " ".join(s["text"] for s in seg_list)
    return TranscriptionResult(text=full_text, language=info.language, segments=seg_list)


def _load_model(model: str):
    if model not in _model_cache:
        from faster_whisper import WhisperModel

        print(f"[stt] Loading faster-whisper {model}...")
        _model_cache[model] = WhisperModel(model, device="cuda", compute_type="float16")
        print("[stt] Model loaded.")
    return _model_cache[model]
