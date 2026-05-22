"""Public library API for voiceCLI — generate, clone, transcribe speech programmatically."""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voicecli.ports.synthesis import SynthesisPort

from voicecli.api.chunked import clone_chunked, generate_chunked
from voicecli.core.utils import OUTPUT_DIR, STT_OUTPUT_DIR, _Unrestricted

log = logging.getLogger(__name__)


# ── Input validation ────────────────────────────────────────────────────────

_MAX_STRING_LEN = 256
_MAX_TEXT_LEN = 100_000


class ParamValidationError(ValueError):
    """Raised when a public-API parameter fails validation.

    Subclass of ValueError so existing `except ValueError` callers keep
    working; NATS adapters catch this narrower type to avoid
    misclassifying path-escape or engine ValueErrors as param faults.
    """


def _check_str(name: str, value, *, max_len: int = _MAX_STRING_LEN) -> None:
    """Validate a string parameter: type, length, no embedded newlines."""
    if value is None:
        return
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string, got {type(value).__name__}")
    if len(value) > max_len:
        raise ParamValidationError(f"{name} exceeds maximum length ({max_len} chars)")
    if "\n" in value or "\r" in value:
        raise ParamValidationError(f"{name} must not contain newline characters")


def _check_float(name: str, value, lo: float, hi: float) -> None:
    """Validate a float parameter: type, finite, within range."""
    if value is None:
        return
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number, got bool")
    if not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    if math.isnan(value) or math.isinf(value):
        raise ParamValidationError(f"{name} must be finite, got {value}")
    if not (lo <= value <= hi):
        raise ParamValidationError(f"{name} must be between {lo} and {hi}, got {value}")


def _check_int(name: str, value, lo: int, hi: int) -> None:
    """Validate an integer parameter: type, within range."""
    if value is None:
        return
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer, got bool")
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, got {type(value).__name__}")
    if not (lo <= value <= hi):
        raise ParamValidationError(f"{name} must be between {lo} and {hi}, got {value}")


def _validate_tts_params(
    *,
    text: str | Path | None = None,
    engine: str | None = None,
    voice: str | None = None,
    language: str | None = None,
    segment_gap: int | None = None,
    crossfade: int | None = None,
    chunk_size: int | None = None,
    extra_kwargs: dict | None = None,
) -> None:
    """Validate TTS parameters at the API boundary.

    Raises TypeError or ValueError for invalid input.
    """
    # Text length (only for raw strings, not file paths)
    if isinstance(text, str) and Path(text).suffix not in (".md", ".txt"):
        _check_str("text", text, max_len=_MAX_TEXT_LEN)

    _check_str("engine", engine, max_len=64)
    _check_str("voice", voice)
    _check_str("language", language)
    _check_int("segment_gap", segment_gap, 0, 30_000)
    _check_int("crossfade", crossfade, 0, 10_000)
    _check_int("chunk_size", chunk_size, 1, 10_000)

    if extra_kwargs:
        for field in ("instruct", "accent", "personality", "speed", "emotion"):
            _check_str(field, extra_kwargs.get(field))
        _check_float("exaggeration", extra_kwargs.get("exaggeration"), 0.0, 2.0)
        _check_float("cfg_weight", extra_kwargs.get("cfg_weight"), 0.0, 1.0)


def _validate_output_path(
    output_path: Path,
    *,
    allowed_base: Path | _Unrestricted,
) -> Path:
    """Validate output path stays within allowed_base; create parent dirs.

    Args:
        output_path: Target output path.
        allowed_base: Base directory the path must stay within, or
            ``UNRESTRICTED`` when the caller owns the trust boundary (CLI
            ``--output`` override, server-controlled scratch path). Pass
            ``UNRESTRICTED`` explicitly — there is no implicit default, so
            each caller declares its trust model.

    Returns:
        Resolved absolute path. For ``UNRESTRICTED`` no parent dirs are
        created (caller is responsible).

    Raises:
        ValueError: If ``output_path`` escapes ``allowed_base``.
    """
    resolved = output_path.expanduser().resolve()

    if isinstance(allowed_base, _Unrestricted):
        return resolved

    base = allowed_base.expanduser().resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        raise ValueError(
            f"Output path is outside the allowed directory {allowed_base}. "
            "Pass an explicit --output to override."
        )
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


@dataclass
class TTSResult:
    """Result of a TTS generation or cloning operation."""

    wav_path: Path
    mp3_path: Path | None = None
    chunk_paths: list[Path] | None = None


# ── Config resolution ────────────────────────────────────────────────────────


def _resolve_config(
    *,
    engine: str | None,
    language: str | None,
    voice: str | None,
    mp3: bool,
    fast: bool,
    chunked: bool,
    chunk_size: int | None,
    plain: bool,
    segment_gap: int | None,
    crossfade: int | None,
    config: Path | None,
    extra_kwargs: dict | None,
) -> dict:
    """Load voicecli.toml and layer CLI/API kwargs over config defaults.

    Returns a dict with all resolved values.
    """
    from voicecli.core.config import load_defaults

    cfg = load_defaults(config)
    kw: dict = extra_kwargs.copy() if extra_kwargs else {}

    # Layer defaults: API kwarg > voicecli.toml > hardcoded
    cli_engine = engine  # preserve caller state before fallback
    r_engine = engine or cfg.get("engine", "qwen")
    r_language = language or cfg.get("language", "English")
    cli_voice = voice  # preserve caller state before toml fallback
    r_voice = voice or cfg.get("voice")
    r_plain = plain or cfg.get("plain", False)
    r_chunked = chunked or cfg.get("chunked", False)
    r_chunk_size = chunk_size if chunk_size is not None else cfg.get("chunk_size", 500)

    # Numeric defaults from config
    for field in (
        "exaggeration",
        "cfg_weight",
        "flow_steps",
        "cfg_alpha",
        "temperature",
        "top_p",
        "min_p",
        "repetition_penalty",
    ):
        if field not in kw and field in cfg:
            kw[field] = cfg[field]

    # Instruct: API kwargs > config raw > composed from (kwargs + config parts)
    if "instruct" not in kw:
        if "instruct" in cfg:
            kw["instruct"] = cfg["instruct"]
        else:
            from voicecli.api.markdown import compose_instruct

            composed = compose_instruct(
                kw.get("accent") or cfg.get("accent"),
                kw.get("personality") or cfg.get("personality"),
                kw.get("speed") or cfg.get("speed"),
                kw.get("emotion") or cfg.get("emotion"),
            )
            if composed:
                kw["instruct"] = composed

    # Segment gap / crossfade: API kwarg > config > 0
    gap_ms = segment_gap if segment_gap is not None else cfg.get("segment_gap", 0)
    xfade_ms = crossfade if crossfade is not None else cfg.get("crossfade", 0)

    return {
        "engine": r_engine,
        "language": r_language,
        "voice": r_voice,
        "cli_engine": cli_engine,
        "cli_voice": cli_voice,
        "mp3": mp3,
        "fast": fast,
        "plain": r_plain,
        "chunked": r_chunked,
        "chunk_size": r_chunk_size,
        "gap_ms": gap_ms,
        "xfade_ms": xfade_ms,
        "extra_kwargs": kw,
        "cfg": cfg,
    }


# ── Input resolution ─────────────────────────────────────────────────────────


def _apply_config_defaults(doc, cfg: dict) -> None:
    """Backfill structured instruct parts from voicecli.toml into doc/segments."""
    from voicecli.api.markdown import compose_instruct

    PARTS = ("accent", "personality", "speed", "emotion")
    cfg_parts = {p: cfg.get(p) for p in PARTS if cfg.get(p)}
    if not cfg_parts:
        return

    for part, val in cfg_parts.items():
        if getattr(doc, part) is None:
            setattr(doc, part, val)

    if not doc.instruct:
        doc.instruct = compose_instruct(doc.accent, doc.personality, doc.speed, doc.emotion)

    for seg in doc.segments:
        has_parts = any(getattr(seg, p) for p in PARTS)
        if seg.instruct and not has_parts:
            continue
        changed = False
        for part, val in cfg_parts.items():
            if getattr(seg, part) is None:
                setattr(seg, part, val)
                changed = True
        if changed:
            composed = compose_instruct(seg.accent, seg.personality, seg.speed, seg.emotion)
            if composed:
                seg.instruct = composed


def _flatten_doc(doc) -> None:
    """Strip [tags] and merge all segments into one, ignoring per-section directives."""
    from voicecli.api.translate import _strip_tags

    if doc.segments:
        texts = [_strip_tags(seg.text) for seg in doc.segments]
        doc.text = " ".join(t for t in texts if t)
        doc.segments = []
    else:
        doc.text = _strip_tags(doc.text)


def _resolve_input(text: str | Path, resolved: dict) -> dict:
    """Detect file input, parse markdown, apply config backfill, translate for engine.

    Mutates and returns `resolved` dict with updated text, engine, language, voice, extra_kwargs.
    """
    text_path = Path(text) if isinstance(text, str) else text
    script_stem: str | None = None

    engine = resolved["engine"]
    cli_engine = resolved["cli_engine"]
    language = resolved["language"]
    voice = resolved["voice"]
    cli_voice = resolved["cli_voice"]
    plain = resolved["plain"]
    cfg = resolved["cfg"]
    kw = resolved["extra_kwargs"]
    gap_ms = resolved["gap_ms"]
    xfade_ms = resolved["xfade_ms"]

    if text_path.suffix == ".txt" and text_path.exists():
        script_stem = text_path.stem
        text = text_path.read_text(encoding="utf-8")
    elif text_path.suffix == ".md" and text_path.exists():
        from voicecli.api.markdown import parse_md_file
        from voicecli.api.translate import translate_for_engine

        script_stem = text_path.stem
        doc = parse_md_file(text_path)
        if doc.engine and cli_engine is None:
            engine = doc.engine
        _apply_config_defaults(doc, cfg)
        if plain:
            _flatten_doc(doc)
        doc = translate_for_engine(doc, engine)
        text = doc.text
        if doc.language:
            language = doc.language
        if doc.voice and cli_voice is None:
            voice = doc.voice
        if doc.instruct:
            kw["instruct"] = doc.instruct
        if doc.exaggeration is not None:
            kw["exaggeration"] = doc.exaggeration
        if doc.cfg_weight is not None:
            kw["cfg_weight"] = doc.cfg_weight
        if doc.flow_steps is not None:
            kw["flow_steps"] = doc.flow_steps
        if doc.cfg_alpha is not None:
            kw["cfg_alpha"] = doc.cfg_alpha
        if doc.temperature is not None:
            kw["temperature"] = doc.temperature
        if doc.top_p is not None:
            kw["top_p"] = doc.top_p
        if doc.min_p is not None:
            kw["min_p"] = doc.min_p
        if doc.repetition_penalty is not None:
            kw["repetition_penalty"] = doc.repetition_penalty
        if doc.segments and len(doc.segments) > 1:
            kw["segments"] = doc.segments
        if resolved.get("_segment_gap_from_caller") is None and doc.segment_gap is not None:
            gap_ms = doc.segment_gap
        if resolved.get("_crossfade_from_caller") is None and doc.crossfade is not None:
            xfade_ms = doc.crossfade
    elif plain:
        from voicecli.api.translate import _strip_tags

        text = _strip_tags(str(text))
    else:
        text = str(text)

    if gap_ms > 0:
        kw["segment_gap"] = gap_ms
    if xfade_ms > 0:
        kw["crossfade"] = xfade_ms

    resolved["text"] = text
    resolved["engine"] = engine
    resolved["language"] = language
    resolved["voice"] = voice
    resolved["script_stem"] = script_stem
    resolved["extra_kwargs"] = kw
    resolved["gap_ms"] = gap_ms
    resolved["xfade_ms"] = xfade_ms
    return resolved


# ── Ref resolution ───────────────────────────────────────────────────────────


def _resolve_ref(ref: Path | str | None) -> Path:
    """Resolve clone reference audio. Falls back to active sample."""
    if ref is not None:
        ref = Path(ref)
        if not ref.exists():
            raise FileNotFoundError(f"Reference audio not found: {ref}")
        return ref

    from voicecli.core.samples import get_active_path

    active = get_active_path()
    if active is None:
        raise ValueError(
            "No --ref provided and no active sample set. "
            "Use 'voicecli samples use <name>' to set an active sample."
        )
    return active


# ── Chunked output helpers ──────────────────────────────────────────────────
# Implementation lives in voicecli.api_chunked; imported at module top.


# ── Public API ───────────────────────────────────────────────────────────────


def generate(
    text: str | Path,
    *,
    engine: str | None = None,
    voice: str | None = None,
    output: str | Path | None = None,
    language: str | None = None,
    mp3: bool = False,
    fast: bool = False,
    chunked: bool = False,
    chunk_size: int | None = None,
    config: str | Path | None = None,
    segment_gap: int | None = None,
    crossfade: int | None = None,
    plain: bool = False,
    allowed_base: Path | _Unrestricted = OUTPUT_DIR,
    _synthesis: "SynthesisPort | None" = None,
    **kwargs,
) -> TTSResult:
    """Generate speech from text or a markdown file using a built-in voice.

    Args:
        text: Text to synthesize, or path to a .md/.txt file.
        engine: TTS engine name (default: from config or "qwen").
        voice: Built-in voice name.
        output: Output WAV path (auto-generated if omitted).
        language: Language name (default: from config or "English").
        mp3: Also save as MP3.
        fast: Use smaller 0.6B Qwen model.
        chunked: Output each chunk as a separate file.
        chunk_size: Target chunk size in characters.
        config: Explicit path to voicecli.toml.
        segment_gap: Silence between segments (ms).
        crossfade: Fade between segments (ms).
        plain: Ignore [tags] and directives.
        allowed_base: Base directory ``output`` must stay within
            (default: ``OUTPUT_DIR``). Pass ``UNRESTRICTED`` when the caller
            has already vetted the path (CLI ``--output``, server scratch dir).
        _synthesis: SynthesisPort implementation to use. If None, defaults to
            DaemonSynthesisAdapter (daemon socket with local engine fallback).
        **kwargs: Additional engine-specific parameters.

    Returns:
        TTSResult with wav_path and optional mp3_path.

    Raises:
        TypeError: Wrong parameter type.
        ValueError: Invalid engine name or parameter out of range.
        FileNotFoundError: Script file not found.
        RuntimeError: CUDA/GPU error.
    """
    _validate_tts_params(
        text=text,
        engine=engine,
        voice=voice,
        language=language,
        segment_gap=segment_gap,
        crossfade=crossfade,
        chunk_size=chunk_size,
        extra_kwargs=kwargs,
    )

    from voicecli.engines.engine import QWEN_ENGINES
    from voicecli.core.utils import build_output_prefix, default_output_path

    if _synthesis is None:
        from voicecli.adapters.synthesis import DaemonSynthesisAdapter  # type: ignore[import-not-found]

        _synthesis = DaemonSynthesisAdapter()
    assert _synthesis is not None

    config_path = Path(config) if config is not None else None

    resolved = _resolve_config(
        engine=engine,
        language=language,
        voice=voice,
        mp3=mp3,
        fast=fast,
        chunked=chunked,
        chunk_size=chunk_size,
        plain=plain,
        segment_gap=segment_gap,
        crossfade=crossfade,
        config=config_path,
        extra_kwargs=kwargs,
    )
    # Track whether caller explicitly set segment_gap/crossfade
    resolved["_segment_gap_from_caller"] = segment_gap
    resolved["_crossfade_from_caller"] = crossfade

    resolved = _resolve_input(text, resolved)

    r_engine = resolved["engine"]
    r_text = resolved["text"]
    r_language = resolved["language"]
    r_voice = resolved["voice"]
    r_chunked = resolved["chunked"]
    r_chunk_size = resolved["chunk_size"]
    r_fast = resolved["fast"]
    r_mp3 = resolved["mp3"]
    script_stem = resolved["script_stem"]
    extra = resolved["extra_kwargs"]

    # Validate output path BEFORE loading engine (security)
    prefix = build_output_prefix(r_engine, script=script_stem, voice=r_voice, language=r_language)
    if output is not None:
        out = _validate_output_path(Path(output), allowed_base=allowed_base)
    else:
        # default_output_path writes inside OUTPUT_DIR by construction
        out = default_output_path(prefix)

    if r_chunked:
        chunk_fn = getattr(_synthesis, "_chunk_fn", None)
        daemon_fn = (
            chunk_fn(r_engine) if r_engine in QWEN_ENGINES and chunk_fn is not None else None
        )
        registry = getattr(_synthesis, "_registry", None)
        if registry is not None:
            eng = registry.get(r_engine)
        else:
            from voicecli.engines.engine import get_engine

            eng = get_engine(r_engine)
        if r_fast and r_engine in QWEN_ENGINES:
            eng.set_small_mode()  # pyright: ignore[reportAttributeAccessIssue]  # Qwen-only
        chunk_paths = generate_chunked(
            eng,
            r_text,
            r_voice,
            out,
            r_language,
            extra,
            chunk_size=r_chunk_size,
            segments=extra.pop("segments", None),
            mp3=r_mp3,
            daemon_fn=daemon_fn,
        )
        return TTSResult(wav_path=out.with_suffix(".done"), chunk_paths=chunk_paths)

    result = _synthesis.generate(
        r_engine,
        r_text,
        r_voice,
        out,
        language=r_language,
        fast=r_fast,
        **extra,
    )
    if result is not None:
        out = result
        mp3_path = None
        if r_mp3:
            from voicecli.core.utils import wav_to_mp3

            mp3_path = wav_to_mp3(out)
        return TTSResult(wav_path=out, mp3_path=mp3_path)

    # Adapter returned None — should not happen (adapter handles its own fallback),
    # but guard defensively.
    raise RuntimeError(f"Synthesis failed for engine '{r_engine}': adapter returned None")


def clone(
    text: str | Path,
    *,
    ref: str | Path | None = None,
    engine: str | None = None,
    ref_text: str | None = None,
    output: str | Path | None = None,
    language: str | None = None,
    mp3: bool = False,
    fast: bool = False,
    chunked: bool = False,
    chunk_size: int | None = None,
    config: str | Path | None = None,
    segment_gap: int | None = None,
    crossfade: int | None = None,
    plain: bool = False,
    allowed_base: Path | _Unrestricted = OUTPUT_DIR,
    _synthesis: "SynthesisPort | None" = None,
    **kwargs,
) -> TTSResult:
    """Clone a voice from reference audio and synthesize text.

    Args:
        text: Text to synthesize, or path to a .md/.txt file.
        ref: Reference audio path for voice cloning (falls back to active sample).
        engine: TTS engine name (default: from config or "qwen").
        ref_text: Transcript of reference audio.
        output: Output WAV path (auto-generated if omitted).
        language: Language name (default: from config or "English").
        mp3: Also save as MP3.
        fast: Use smaller 0.6B Qwen model.
        chunked: Output each chunk as a separate file.
        chunk_size: Target chunk size in characters.
        config: Explicit path to voicecli.toml.
        segment_gap: Silence between segments (ms).
        crossfade: Fade between segments (ms).
        plain: Ignore [tags] and directives.
        allowed_base: Base directory ``output`` must stay within
            (default: ``OUTPUT_DIR``). Pass ``UNRESTRICTED`` when the caller
            has already vetted the path.
        _synthesis: SynthesisPort implementation to use. If None, defaults to
            DaemonSynthesisAdapter (daemon socket with local engine fallback).
        **kwargs: Additional engine-specific parameters.

    Returns:
        TTSResult with wav_path and optional mp3_path.

    Raises:
        TypeError: Wrong parameter type.
        ValueError: Invalid engine name, no active sample, or parameter out of range.
        FileNotFoundError: Reference audio or script file not found.
        RuntimeError: CUDA/GPU error.
    """
    _validate_tts_params(
        text=text,
        engine=engine,
        voice=None,
        language=language,
        segment_gap=segment_gap,
        crossfade=crossfade,
        chunk_size=chunk_size,
        extra_kwargs=kwargs,
    )
    _check_str("ref_text", ref_text)

    from voicecli.engines.engine import QWEN_ENGINES
    from voicecli.core.utils import build_output_prefix, default_output_path

    if _synthesis is None:
        from voicecli.adapters.synthesis import DaemonSynthesisAdapter  # type: ignore[import-not-found]

        _synthesis = DaemonSynthesisAdapter()
    assert _synthesis is not None

    ref_path = _resolve_ref(ref)
    config_path = Path(config) if config is not None else None

    resolved = _resolve_config(
        engine=engine,
        language=language,
        voice=None,
        mp3=mp3,
        fast=fast,
        chunked=chunked,
        chunk_size=chunk_size,
        plain=plain,
        segment_gap=segment_gap,
        crossfade=crossfade,
        config=config_path,
        extra_kwargs=kwargs,
    )
    resolved["_segment_gap_from_caller"] = segment_gap
    resolved["_crossfade_from_caller"] = crossfade

    resolved = _resolve_input(text, resolved)

    r_engine = resolved["engine"]
    r_text = resolved["text"]
    r_language = resolved["language"]
    r_chunked = resolved["chunked"]
    r_chunk_size = resolved["chunk_size"]
    r_fast = resolved["fast"]
    r_mp3 = resolved["mp3"]
    script_stem = resolved["script_stem"]
    extra = resolved["extra_kwargs"]

    # Validate output path BEFORE loading engine (security)
    prefix = build_output_prefix(r_engine, script=script_stem, language=r_language, clone=True)
    if output is not None:
        out = _validate_output_path(Path(output), allowed_base=allowed_base)
    else:
        # default_output_path writes inside OUTPUT_DIR by construction
        out = default_output_path(prefix)

    if r_chunked:
        chunk_fn = getattr(_synthesis, "_chunk_fn", None)
        daemon_fn = (
            chunk_fn(r_engine) if r_engine in QWEN_ENGINES and chunk_fn is not None else None
        )
        registry = getattr(_synthesis, "_registry", None)
        if registry is not None:
            eng = registry.get(r_engine)
        else:
            from voicecli.engines.engine import get_engine

            eng = get_engine(r_engine)
        if r_fast and r_engine in QWEN_ENGINES:
            eng.set_small_mode()  # pyright: ignore[reportAttributeAccessIssue]  # Qwen-only
        chunk_paths = clone_chunked(
            eng,
            r_text,
            ref_path,
            ref_text,
            out,
            r_language,
            extra,
            chunk_size=r_chunk_size,
            segments=extra.pop("segments", None),
            mp3=r_mp3,
            daemon_fn=daemon_fn,
        )
        return TTSResult(wav_path=out.with_suffix(".done"), chunk_paths=chunk_paths)

    result = _synthesis.clone(
        r_engine,
        r_text,
        ref_path,
        out,
        ref_text=ref_text,
        language=r_language,
        fast=r_fast,
        **extra,
    )
    if result is not None:
        out = result
        mp3_path = None
        if r_mp3:
            from voicecli.core.utils import wav_to_mp3

            mp3_path = wav_to_mp3(out)
        return TTSResult(wav_path=out, mp3_path=mp3_path)

    # Adapter returned None — should not happen (adapter handles its own fallback),
    # but guard defensively.
    raise RuntimeError(f"Clone failed for engine '{r_engine}': adapter returned None")


def transcribe(
    audio: str | Path,
    *,
    model: str = "large-v3-turbo",
    language: str | None = None,
    output: str | Path | None = None,
    language_detection_threshold: float | None = None,
    language_detection_segments: int | None = None,
    language_fallback: str | None = None,
    initial_prompt: str | None = None,
    task: str = "transcribe",
    _skip_daemon: bool = False,
    allowed_base: Path | _Unrestricted = STT_OUTPUT_DIR,
):
    """Transcribe an audio file to text.

    Args:
        audio: Path to audio file.
        model: Whisper model name.
        language: Force language code.
        output: Save transcription text to file.
        language_detection_threshold: Confidence threshold below which fallback language is used.
        language_detection_segments: Number of segments to sample for language detection.
        language_fallback: Language code to use when detection confidence is below threshold.
        initial_prompt: Whisper decoder context — biases punctuation/casing/vocabulary.
        task: "transcribe" (default) or "translate".
        _skip_daemon: Bypass Unix-socket daemon and run inference locally (private).
        allowed_base: Base directory ``output`` must stay within
            (default: ``STT_OUTPUT_DIR``). Pass ``UNRESTRICTED`` for an
            explicit caller-provided path.

    Returns:
        TranscriptionResult with .text, .language, .segments.

    Raises:
        FileNotFoundError: Audio file not found.
        ValueError: Invalid model name.
    """
    from voicecli.runtime.transcribe import TranscriptionResult  # noqa: F811
    from voicecli.runtime.transcribe import transcribe as _transcribe

    audio_path = Path(audio)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    result: TranscriptionResult = _transcribe(
        audio_path,
        model=model,
        language=language,
        language_detection_threshold=language_detection_threshold,
        language_detection_segments=language_detection_segments,
        language_fallback=language_fallback,
        initial_prompt=initial_prompt,
        task=task,
        _skip_daemon=_skip_daemon,
    )

    if output is not None:
        out_path = _validate_output_path(Path(output), allowed_base=allowed_base)
        out_path.write_text(result.text, encoding="utf-8")

    return result


def list_engines() -> list[str]:
    """Return available TTS engine names."""
    from voicecli.engines.engine import available_engines

    return available_engines()


def list_voices(engine: str) -> list[str]:
    """Return available voice names for an engine.

    Raises:
        ValueError: If engine name is unknown.
    """
    from voicecli.engines.engine import get_engine

    try:
        eng = get_engine(engine)
    except ValueError:
        raise ValueError(f"Unknown engine '{engine}'. Available: {list_engines()}")
    return eng.list_voices()


def warmup_model(model: str) -> None:
    """Pre-load a faster-whisper STT model into VRAM.

    Public façade over transcribe._load_model so callers (e.g. the NATS STT
    adapter) do not need to import private symbols directly.

    Args:
        model: Model name accepted by faster-whisper (e.g. "large-v3-turbo").
    """
    from voicecli.runtime.transcribe import _load_model

    _load_model(model)


# ── Async wrappers ───────────────────────────────────────────────────────────


async def generate_async(*args, **kwargs) -> TTSResult:
    """Async wrapper for generate() — runs in a thread for event loop integration."""
    return await asyncio.to_thread(generate, *args, **kwargs)


async def clone_async(*args, **kwargs) -> TTSResult:
    """Async wrapper for clone() — runs in a thread for event loop integration."""
    return await asyncio.to_thread(clone, *args, **kwargs)


async def transcribe_async(*args, **kwargs):
    """Async wrapper for transcribe() — runs in a thread for event loop integration."""
    return await asyncio.to_thread(transcribe, *args, **kwargs)
