"""File-based speech-to-text using Faster Whisper."""

from dataclasses import dataclass
from pathlib import Path

MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
DEFAULT_MODEL = "large-v3-turbo"

VALID_MODELS = frozenset(
    {
        "tiny",
        "tiny.en",
        "base",
        "base.en",
        "small",
        "small.en",
        "medium",
        "medium.en",
        "large",
        "large-v2",
        "large-v3",
        "large-v3-turbo",
        "distil-large-v2",
        "distil-large-v3",
        "distil-medium.en",
        "distil-small.en",
    }
)

_model_cache: dict[str, object] = {}


@dataclass
class TranscriptionResult:
    text: str
    language: str | None  # detected language code ("en", "fr", ...)
    segments: list[dict]  # [{start, end, text}, ...]


def transcribe(
    audio_path: Path,
    model: str = DEFAULT_MODEL,
    language: str | None = None,
    language_detection_threshold: float | None = None,
    language_detection_segments: int | None = None,
    language_fallback: str | None = None,
) -> TranscriptionResult:
    whisper = _load_model(model)

    # If threshold + fallback are set, run a fast language detection pass first
    if (
        language is None
        and language_detection_threshold is not None
        and language_fallback is not None
    ):
        detect_kwargs: dict = {}
        if language_detection_segments is not None:
            detect_kwargs["language_detection_segments"] = language_detection_segments
        _, detect_info = whisper.transcribe(
            str(audio_path),
            language=None,
            task="transcribe",
            beam_size=1,
            vad_filter=True,
            **detect_kwargs,
        )
        if detect_info.language_probability < language_detection_threshold:
            print(
                f"[stt] low confidence ({detect_info.language_probability:.2f}) for '{detect_info.language}', falling back to '{language_fallback}'",
                file=__import__("sys").stderr,
            )
            language = language_fallback
        else:
            language = detect_info.language

    kwargs: dict = dict(
        language=language,
        task="transcribe",
        beam_size=5,
        vad_filter=True,
    )
    if language_detection_threshold is not None and language_fallback is None:
        kwargs["language_detection_threshold"] = language_detection_threshold
    if language_detection_segments is not None:
        kwargs["language_detection_segments"] = language_detection_segments
    segments, info = whisper.transcribe(str(audio_path), **kwargs)
    seg_list = []
    for s in segments:
        seg_list.append({"start": s.start, "end": s.end, "text": s.text.strip()})
        duration = s.end - s.start
        print(
            f"[stt] segment [{s.start:.2f}s–{s.end:.2f}s, {duration:.2f}s]: {s.text.strip()}",
            file=__import__("sys").stderr,
        )
    full_text = " ".join(s["text"] for s in seg_list)
    return TranscriptionResult(text=full_text, language=info.language, segments=seg_list)


def warmup(model: str = DEFAULT_MODEL) -> None:
    """Pre-load model into VRAM (eager load at daemon startup)."""
    _load_model(model)


def _load_model(model: str):
    if model not in VALID_MODELS:
        raise ValueError(
            f"Unknown model '{model}'. Valid models: {', '.join(sorted(VALID_MODELS))}"
        )
    if model not in _model_cache:
        from faster_whisper import WhisperModel

        print(f"[stt] Loading faster-whisper {model}...")
        _model_cache[model] = WhisperModel(model, device="cuda", compute_type="float16")
        print("[stt] Model loaded.")
    return _model_cache[model]
