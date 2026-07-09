"""File-based STT via Faster Whisper. Daemon-first: forwards to warm model over Unix socket,
falls back to local load if unavailable."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

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

_model_cache: dict[str, WhisperModel] = {}
_model_lock = threading.Lock()

# VAD splits on pauses; cross-chunk punctuation uses an explicit carry prompt.
_VAD_PARAMETERS = {"min_silence_duration_ms": 500, "speech_pad_ms": 400}
# Whisper initial_prompt ≈224 tokens — keep the carry tail short.
_MAX_CARRY_PROMPT_CHARS = 200

# Known Whisper hallucination signatures (YouTube/TV subtitle closings).
# Case-insensitive match; stripped from tail and from standalone mid-text sentences.

# Generic regex patterns for Whisper hallucination classes.
# Anchored to end-of-text ($) so mid-sentence legitimate mentions are NOT matched.
# Leading whitespace/dashes are consumed to handle "– Sous-titrage FR 2021" variants.
# The pattern requires at least one non-whitespace token AFTER the keyword to distinguish
# "Sous-titrage FR 2021" (hallucination) from "Le sous-titrage est important" (legit).
_HALLUCINATION_REGEXES = (
    # "Sous-titrage <broadcaster> <year/num>" — FR TV/radio broadcaster IDs
    # Covers: "Sous-titrage FR 2021", "Sous-titres FR 2024", "Sous-titrage TF1 2019",
    #         "Sous-titrage Société Radio-Canada", "Sous-titrage ST' 501", etc.
    # Requires the keyword to follow a sentence boundary (period, start, or dash/whitespace
    # after period) so bare "Le sous-titrage est important" is not matched.
    re.compile(
        r"(?:(?<=\.)|^)[.\s\-–—]*sous-titr(?:e|es|age)\s+\S[^.]*$",
        re.IGNORECASE,
    ),
    # "Captions by <name>" — English equivalent
    re.compile(r"(?:(?<=\.)|^)[.\s\-–—]*captions?\s+by\s+\S[^.]*$", re.IGNORECASE),
)

_HALLUCINATIONS = frozenset(
    {
        "sous-titrage société radio-canada",
        "sous-titrage st' 501",
        "sous-titres réalisés par la communauté d'amara.org",
        "sous-titres réalisés par les sous-titreurs amara.org",
        "sous-titres faits par la communauté d'amara.org",
        "❤️ par soustitreur.com",
        "par soustitreur.com",
        "merci d'avoir regardé cette vidéo",
        "merci d'avoir regardé la vidéo",
        "n'oubliez pas de vous abonner",
        "abonnez-vous à la chaîne",
        "thanks for watching",
        "thank you for watching",
        "please subscribe",
        "don't forget to subscribe",
        "subtitles by the amara.org community",
    }
)


def _strip_hallucinations(text: str) -> str:
    """Strip known Whisper hallucination signatures from text."""
    import sys

    # Tail pass — loop handles chained hallucinations (e.g. two appended).
    # Each iteration applies literal patterns first (faster/more specific),
    # then regex patterns.  Max 5 iterations to avoid infinite loops.
    for _ in range(5):
        changed = False

        # Literal pattern pass.
        norm = text.lower().rstrip(" .,!?")
        for pat in _HALLUCINATIONS:
            if norm.endswith(pat):
                start = len(norm) - len(pat)
                print(f"[stt] stripped hallucination: {text[start:].strip()!r}", file=sys.stderr)
                # Strip only leading punctuation/whitespace separators before the hallucination,
                # not the legitimate trailing period of the preceding sentence.
                text = text[:start].rstrip(" \t–—-")
                changed = True
                break

        # Regex pattern pass — handles broadcaster+year variants and leading dashes.
        for rx in _HALLUCINATION_REGEXES:
            new_text = rx.sub("", text)
            if new_text != text:
                matched = text[len(new_text) :]
                print(
                    f"[stt] stripped hallucination (regex): {matched.strip()!r}",
                    file=sys.stderr,
                )
                # Strip trailing whitespace only — preserve legitimate punctuation
                # (e.g. the period in "Mon vrai texte. – Sous-titrage FR 2021").
                text = new_text.rstrip(" \t")
                changed = True
                break

        if not changed:
            break

    # Sentence pass — remove mid-text standalone hallucination sentences.
    parts = [s.strip() for s in text.split(".") if s.strip()]
    clean = [s for s in parts if s.lower().rstrip(" .,!?") not in _HALLUCINATIONS]
    for removed in set(parts) - set(clean):
        print(f"[stt] stripped hallucination: {removed!r}", file=sys.stderr)
    if len(clean) != len(parts):
        text = ". ".join(clean)
        if text and not text.endswith("."):
            text += "."

    return text


def _build_carry_prompt(base: str | None, accumulated: str) -> str | None:
    """Merge mode/vocab prompt with cleaned text from earlier VAD chunks."""
    tail = accumulated.strip()
    if len(tail) > _MAX_CARRY_PROMPT_CHARS:
        tail = tail[-_MAX_CARRY_PROMPT_CHARS:].strip()
    if not tail:
        return base
    if base:
        return f"{base} {tail}"
    return tail


def _decode_kwargs(
    *,
    language: str | None,
    task: str,
    initial_prompt: str | None,
    language_detection_threshold: float | None = None,
    language_detection_segments: int | None = None,
) -> dict:
    kwargs: dict = dict(
        language=language,
        task=task,
        beam_size=5,
        vad_filter=False,
        condition_on_previous_text=True,
        no_speech_threshold=0.7,
        compression_ratio_threshold=2.4,
        initial_prompt=initial_prompt,
    )
    if language_detection_threshold is not None:
        kwargs["language_detection_threshold"] = language_detection_threshold
    if language_detection_segments is not None:
        kwargs["language_detection_segments"] = language_detection_segments
    return kwargs


def _transcribe_with_segment_carry(
    whisper: WhisperModel,
    audio_path: Path,
    *,
    language: str | None,
    task: str,
    initial_prompt: str | None,
    language_detection_threshold: float | None = None,
    language_detection_segments: int | None = None,
) -> tuple[list[Segment], object]:
    """Transcribe each VAD speech region with context carried via initial_prompt."""
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    audio = decode_audio(str(audio_path), sampling_rate=16000)
    vad_opts = VadOptions(**_VAD_PARAMETERS)
    speech_chunks = get_speech_timestamps(audio, vad_opts, sampling_rate=16000)

    if not speech_chunks:
        return _transcribe_single_pass(
            whisper,
            audio_path,
            language=language,
            task=task,
            initial_prompt=initial_prompt,
            language_detection_threshold=language_detection_threshold,
            language_detection_segments=language_detection_segments,
        )

    accumulated = ""
    seg_list = []
    info = None

    for chunk in speech_chunks:
        chunk_audio = audio[chunk["start"] : chunk["end"]]
        offset_s = chunk["start"] / 16000.0
        carry = _build_carry_prompt(initial_prompt, accumulated)
        kwargs = _decode_kwargs(
            language=language,
            task=task,
            initial_prompt=carry,
            language_detection_threshold=language_detection_threshold,
            language_detection_segments=language_detection_segments,
        )
        segments, chunk_info = whisper.transcribe(chunk_audio, **kwargs)
        info = chunk_info
        chunk_parts: list[str] = []
        for s in segments:
            seg_text = _strip_hallucinations(s.text.strip())
            if not seg_text:
                continue
            seg_list.append(Segment(start=s.start + offset_s, end=s.end + offset_s, text=seg_text))
            chunk_parts.append(seg_text)
        if chunk_parts:
            chunk_text = " ".join(chunk_parts)
            accumulated = f"{accumulated} {chunk_text}".strip() if accumulated else chunk_text

    return seg_list, info


def _transcribe_single_pass(
    whisper: WhisperModel,
    audio_path: Path,
    *,
    language: str | None,
    task: str,
    initial_prompt: str | None,
    language_detection_threshold: float | None = None,
    language_detection_segments: int | None = None,
) -> tuple[list[Segment], object]:
    """Legacy single-pass decode (pre-#154 style): no cross-VAD context carry."""
    kwargs: dict = dict(
        language=language,
        task=task,
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
        no_speech_threshold=0.7,
        compression_ratio_threshold=2.4,
        vad_parameters=dict(_VAD_PARAMETERS),
    )
    if initial_prompt is not None:
        kwargs["initial_prompt"] = initial_prompt
    if language_detection_threshold is not None:
        kwargs["language_detection_threshold"] = language_detection_threshold
    if language_detection_segments is not None:
        kwargs["language_detection_segments"] = language_detection_segments
    segments, info = whisper.transcribe(str(audio_path), **kwargs)
    seg_list: list[Segment] = []
    for s in segments:
        seg_text = _strip_hallucinations(s.text.strip())
        if not seg_text:
            continue
        seg_list.append(Segment(start=s.start, end=s.end, text=seg_text))
    return seg_list, info


def _resolve_segment_context_carry(value: bool | None) -> bool:
    if value is not None:
        return value
    from voicecli.core.config import load_stt_config

    return bool(load_stt_config().get("segment_context_carry", True))


@dataclass
class Segment:
    start: float
    end: float
    text: str

    def __post_init__(self) -> None:
        self.start = float(self.start)
        self.end = float(self.end)

    def to_wire_dict(self) -> dict[str, float | str]:
        return {"start": self.start, "end": self.end, "text": self.text}


def segments_to_wire(segments: list[Segment]) -> list[dict[str, float | str]]:
    return [s.to_wire_dict() for s in segments]


@dataclass
class TranscriptionResult:
    text: str
    language: str | None  # detected language code ("en", "fr", ...)
    segments: list[Segment]  # [{start, end, text}, ...]


def _try_daemon(
    audio_path: Path,
    language: str | None,
    language_detection_threshold: float | None,
    language_detection_segments: int | None,
    language_fallback: str | None,
    task: str,
    initial_prompt: str | None,
    segment_context_carry: bool,
) -> TranscriptionResult | None:
    """Try the STT daemon for transcription. Returns None to fall back locally."""
    from voicecli.core.paths import STT_SOCKET_PATH as SOCKET_PATH

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
    req["segment_context_carry"] = segment_context_carry

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
            raw_segments = resp.get("segments") or []
            return TranscriptionResult(
                text=resp.get("text", ""),
                language=resp.get("language"),
                segments=[
                    Segment(start=s["start"], end=s["end"], text=s["text"]) for s in raw_segments
                ],
            )
    except Exception:
        pass
    return None


def transcribe(
    audio_path: Path,
    *,
    model: str = DEFAULT_MODEL,
    language: str | None = None,
    language_detection_threshold: float | None = None,
    language_detection_segments: int | None = None,
    language_fallback: str | None = None,
    task: str = "transcribe",
    initial_prompt: str | None = None,
    segment_context_carry: bool | None = None,
    _skip_daemon: bool = False,
) -> TranscriptionResult:
    from voicecli.core.env import coerce_bool_env

    if model == "mock" and coerce_bool_env("VOICECLI_ENABLE_MOCK_ENGINE"):
        return TranscriptionResult(text="", language="en", segments=[])

    carry = _resolve_segment_context_carry(segment_context_carry)

    # Try daemon first — reuses warm model, avoids loading locally
    if not _skip_daemon:
        daemon_result = _try_daemon(
            audio_path,
            language,
            language_detection_threshold,
            language_detection_segments,
            language_fallback,
            task,
            initial_prompt,
            carry,
        )
        if daemon_result is not None:
            return daemon_result

    whisper = _load_model(model)
    assert whisper is not None  # mock short-circuits at top

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

    detect_threshold = language_detection_threshold if language_fallback is None else None
    decode_fn = _transcribe_with_segment_carry if carry else _transcribe_single_pass
    seg_list, info = decode_fn(
        whisper,
        audio_path,
        language=language,
        task=task,
        initial_prompt=initial_prompt,
        language_detection_threshold=detect_threshold,
        language_detection_segments=language_detection_segments,
    )
    for s in seg_list:
        duration = s.end - s.start
        print(
            f"[stt] segment [{s.start:.2f}s–{s.end:.2f}s, {duration:.2f}s]: {s.text}",
            file=__import__("sys").stderr,
        )
    full_text = _strip_hallucinations(" ".join(s.text for s in seg_list))
    return TranscriptionResult(text=full_text, language=info.language, segments=seg_list)


def warmup(model: str = DEFAULT_MODEL) -> None:
    """Pre-load model into VRAM (eager load at daemon startup)."""
    _load_model(model)


def unload_model() -> None:
    """Unload all cached models and release VRAM."""
    with _model_lock:
        if not _model_cache:
            return
        _model_cache.clear()

    import gc

    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    print("[stt] Models unloaded.")


def _load_model(model: str) -> WhisperModel | None:
    """Load and cache a faster-whisper model. Returns None when mock env gate is set."""
    from voicecli.core.env import coerce_bool_env

    if model == "mock" and coerce_bool_env("VOICECLI_ENABLE_MOCK_ENGINE"):
        return None
    if model not in VALID_MODELS:
        raise ValueError(
            f"Unknown model '{model}'. Valid models: {', '.join(sorted(VALID_MODELS))}"
        )
    with _model_lock:
        if model not in _model_cache:
            from faster_whisper import WhisperModel

            print(f"[stt] Loading faster-whisper {model}...")
            _model_cache[model] = WhisperModel(model, device="cuda", compute_type="float16")
            print("[stt] Model loaded.")
        return _model_cache[model]
