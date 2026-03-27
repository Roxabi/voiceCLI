"""File-based speech-to-text using Faster Whisper.

Daemon-first: if the STT daemon is running, transcribe requests are forwarded
over Unix socket to reuse the warm model. Falls back to local model loading
if the daemon is unavailable.
"""

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


def _try_daemon(
    audio_path: Path,
    language: str | None,
    language_detection_threshold: float | None,
    language_detection_segments: int | None,
    language_fallback: str | None,
    task: str,
    initial_prompt: str | None,
) -> TranscriptionResult | None:
    """Try the STT daemon for transcription. Returns None to fall back locally."""
    from voicecli.stt_daemon import SOCKET_PATH

    if not SOCKET_PATH.exists():
        return None
    import json
    import socket

    req: dict = {
        "action": "transcribe_file",
        "audio_path": str(audio_path.resolve()),
        "task": task,
    }
    if language is not None:
        req["language"] = language
    if language_detection_threshold is not None:
        req["language_detection_threshold"] = language_detection_threshold
    if language_detection_segments is not None:
        req["language_detection_segments"] = language_detection_segments
    if language_fallback is not None:
        req["language_fallback"] = language_fallback
    if initial_prompt is not None:
        req["initial_prompt"] = initial_prompt

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(300)
        try:
            sock.connect(str(SOCKET_PATH))
            sock.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode())
            buf = bytearray()
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf.extend(chunk)
                if b"\n" in buf:
                    break
            resp = json.loads(buf.split(b"\n")[0])
        finally:
            sock.close()

        if resp.get("status") == "ok":
            return TranscriptionResult(
                text=resp.get("text", ""),
                language=resp.get("language"),
                segments=resp.get("segments", []),
            )
    except Exception:
        pass
    return None


def transcribe(
    audio_path: Path,
    model: str = DEFAULT_MODEL,
    language: str | None = None,
    language_detection_threshold: float | None = None,
    language_detection_segments: int | None = None,
    language_fallback: str | None = None,
    task: str = "transcribe",
    initial_prompt: str | None = None,
) -> TranscriptionResult:
    # Try daemon first — reuses warm model, avoids loading locally
    daemon_result = _try_daemon(
        audio_path,
        language,
        language_detection_threshold,
        language_detection_segments,
        language_fallback,
        task,
        initial_prompt,
    )
    if daemon_result is not None:
        return daemon_result

    whisper = _load_model(model)

    # If threshold + fallback are set, run a fast language detection pass first
    # (only applies for transcribe task, not translate)
    if (
        task == "transcribe"
        and language is None
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
        task=task,
        beam_size=5,
        vad_filter=True,
    )
    if initial_prompt is not None:
        kwargs["initial_prompt"] = initial_prompt
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
