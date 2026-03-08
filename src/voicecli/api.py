"""Public library API for voiceCLI — generate, clone, transcribe speech programmatically."""

from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass
from pathlib import Path


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
    from voicecli.config import load_defaults

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
    for field in ("exaggeration", "cfg_weight"):
        if field not in kw and field in cfg:
            kw[field] = cfg[field]

    # Instruct default from config (raw bypass > composed from parts)
    if "instruct" not in kw:
        if "instruct" in cfg:
            kw["instruct"] = cfg["instruct"]
        else:
            from voicecli.markdown import compose_instruct

            composed = compose_instruct(
                cfg.get("accent"),
                cfg.get("personality"),
                cfg.get("speed"),
                cfg.get("emotion"),
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
    from voicecli.markdown import compose_instruct

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
    from voicecli.translate import _strip_tags

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
        from voicecli.markdown import parse_md_file
        from voicecli.translate import translate_for_engine

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
        if doc.segments and len(doc.segments) > 1:
            kw["segments"] = doc.segments
        if resolved.get("_segment_gap_from_caller") is None and doc.segment_gap is not None:
            gap_ms = doc.segment_gap
        if resolved.get("_crossfade_from_caller") is None and doc.crossfade is not None:
            xfade_ms = doc.crossfade
    elif plain:
        from voicecli.translate import _strip_tags

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

    from voicecli.samples import get_active_path

    active = get_active_path()
    if active is None:
        raise ValueError(
            "No --ref provided and no active sample set. "
            "Use 'voicecli samples use <name>' to set an active sample."
        )
    return active


# ── Daemon helpers ───────────────────────────────────────────────────────────


def _try_daemon(request: dict) -> Path | None:
    """Send request to daemon. Returns WAV path on success, None to fall back."""
    from voicecli.daemon import SOCKET_PATH, daemon_request

    if not SOCKET_PATH.exists():
        return None
    try:
        resp = daemon_request(request, timeout=300)
        if resp.get("status") == "ok":
            return Path(resp["path"])
    except Exception:
        pass
    return None


def _make_chunk_daemon_fn(engine_name: str):
    """Return a daemon_fn for chunked generation/cloning."""

    def daemon_fn(method: str, text: str, voice, chunk_path: Path, **kwargs) -> bool:
        req: dict = {
            "action": method,
            "engine": engine_name,
            "text": text,
            "voice": voice,
            "output_path": str(chunk_path.resolve()),
            "language": kwargs.get("language"),
            "instruct": kwargs.get("instruct"),
            "exaggeration": kwargs.get("exaggeration"),
            "cfg_weight": kwargs.get("cfg_weight"),
            "segment_gap": kwargs.get("segment_gap"),
            "crossfade": kwargs.get("crossfade"),
            "segments": [],
        }
        if method == "clone":
            ref = kwargs.get("ref_audio")
            req["ref_audio"] = str(ref.resolve()) if ref else None
            req["ref_text"] = kwargs.get("ref_text")
        return _try_daemon(req) is not None

    return daemon_fn


# ── Chunked output helpers ──────────────────────────────────────────────────


def _emit_chunk(
    eng,
    method: str,
    text: str,
    voice,
    out: Path,
    index: int,
    total: int,
    *,
    mp3: bool = False,
    daemon_fn=None,
    **kwargs,
) -> Path:
    """Generate and save a single numbered chunk. Returns chunk path."""
    stem = out.stem
    chunk_path = out.parent / f"{stem}_{index:03d}.wav"
    if daemon_fn is None or not daemon_fn(method, text, voice, chunk_path, **kwargs):
        if method == "generate":
            eng.generate(text, voice, chunk_path, **kwargs)
        else:
            eng.clone(
                text,
                kwargs.pop("ref_audio"),
                chunk_path,
                ref_text=kwargs.pop("ref_text", None),
                **kwargs,
            )
    if mp3:
        from voicecli.utils import wav_to_mp3

        wav_to_mp3(chunk_path)
    return chunk_path


def _write_done(out: Path) -> Path:
    done_path = out.with_suffix(".done")
    done_path.write_text("done\n")
    return done_path


def _generate_chunked(
    eng,
    text,
    voice,
    out,
    language,
    extra_kwargs,
    *,
    chunk_size,
    segments,
    mp3=False,
    daemon_fn=None,
) -> list[Path]:
    """Generate speech in chunks. Returns list of chunk paths."""
    from voicecli.utils import smart_chunk

    out.parent.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    if segments and len(segments) > 1:
        total = len(segments)
        for i, seg in enumerate(segments, 1):
            kw = {**extra_kwargs, "language": seg.language or language}
            if seg.instruct:
                kw["instruct"] = seg.instruct
            if seg.exaggeration is not None:
                kw["exaggeration"] = seg.exaggeration
            if seg.cfg_weight is not None:
                kw["cfg_weight"] = seg.cfg_weight
            seg_voice = seg.voice or voice
            p = _emit_chunk(
                eng,
                "generate",
                seg.text,
                seg_voice,
                out,
                i,
                total,
                mp3=mp3,
                daemon_fn=daemon_fn,
                **kw,
            )
            paths.append(p)
    else:
        chunks = smart_chunk(text, chunk_size)
        total = len(chunks)
        for i, chunk_text in enumerate(chunks, 1):
            p = _emit_chunk(
                eng,
                "generate",
                chunk_text,
                voice,
                out,
                i,
                total,
                mp3=mp3,
                daemon_fn=daemon_fn,
                language=language,
                **extra_kwargs,
            )
            paths.append(p)

    _write_done(out)
    return paths


def _clone_chunked(
    eng,
    text,
    ref,
    ref_text,
    out,
    language,
    extra_kwargs,
    *,
    chunk_size,
    segments,
    mp3=False,
    daemon_fn=None,
) -> list[Path]:
    """Clone voice in chunks. Returns list of chunk paths."""
    from voicecli.utils import smart_chunk

    out.parent.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    if segments and len(segments) > 1:
        total = len(segments)
        for i, seg in enumerate(segments, 1):
            kw = {
                **extra_kwargs,
                "language": seg.language or language,
                "ref_audio": ref,
                "ref_text": ref_text,
            }
            if seg.exaggeration is not None:
                kw["exaggeration"] = seg.exaggeration
            if seg.cfg_weight is not None:
                kw["cfg_weight"] = seg.cfg_weight
            p = _emit_chunk(
                eng, "clone", seg.text, None, out, i, total, mp3=mp3, daemon_fn=daemon_fn, **kw
            )
            paths.append(p)
    else:
        chunks = smart_chunk(text, chunk_size)
        total = len(chunks)
        for i, chunk_text in enumerate(chunks, 1):
            p = _emit_chunk(
                eng,
                "clone",
                chunk_text,
                None,
                out,
                i,
                total,
                mp3=mp3,
                daemon_fn=daemon_fn,
                language=language,
                ref_audio=ref,
                ref_text=ref_text,
                **extra_kwargs,
            )
            paths.append(p)

    _write_done(out)
    return paths


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
        **kwargs: Additional engine-specific parameters.

    Returns:
        TTSResult with wav_path and optional mp3_path.

    Raises:
        ValueError: Invalid engine name.
        FileNotFoundError: Script file not found.
        RuntimeError: CUDA/GPU error.
    """
    from voicecli.engine import QWEN_ENGINES, get_engine
    from voicecli.utils import build_output_prefix, default_output_path

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

    eng = get_engine(r_engine)
    if r_fast and r_engine in QWEN_ENGINES:
        eng._small = True

    prefix = build_output_prefix(r_engine, script=script_stem, voice=r_voice, language=r_language)
    out = Path(output) if output is not None else default_output_path(prefix)

    if r_chunked:
        daemon_fn = _make_chunk_daemon_fn(r_engine) if r_engine in QWEN_ENGINES else None
        chunk_paths = _generate_chunked(
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

    if r_engine in QWEN_ENGINES:
        daemon_result = _try_daemon(
            {
                "action": "generate",
                "engine": r_engine,
                "text": r_text,
                "voice": r_voice,
                "output_path": str(out.resolve()),
                "language": r_language,
                "instruct": extra.get("instruct"),
                "exaggeration": extra.get("exaggeration"),
                "cfg_weight": extra.get("cfg_weight"),
                "segment_gap": extra.get("segment_gap"),
                "crossfade": extra.get("crossfade"),
                "segments": [dataclasses.asdict(s) for s in (extra.get("segments") or [])],
            }
        )
        if daemon_result:
            out = daemon_result
            mp3_path = None
            if r_mp3:
                from voicecli.utils import wav_to_mp3

                mp3_path = wav_to_mp3(out)
            return TTSResult(wav_path=out, mp3_path=mp3_path)

    out = eng.generate(r_text, r_voice, out, language=r_language, **extra)

    mp3_path = None
    if r_mp3:
        from voicecli.utils import wav_to_mp3

        mp3_path = wav_to_mp3(out)

    return TTSResult(wav_path=out, mp3_path=mp3_path)


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
        **kwargs: Additional engine-specific parameters.

    Returns:
        TTSResult with wav_path and optional mp3_path.

    Raises:
        ValueError: Invalid engine name or no active sample set.
        FileNotFoundError: Reference audio or script file not found.
        RuntimeError: CUDA/GPU error.
    """
    from voicecli.engine import QWEN_ENGINES, get_engine
    from voicecli.utils import build_output_prefix, default_output_path

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

    eng = get_engine(r_engine)
    if r_fast and r_engine in QWEN_ENGINES:
        eng._small = True

    prefix = build_output_prefix(r_engine, script=script_stem, language=r_language, clone=True)
    out = Path(output) if output is not None else default_output_path(prefix)

    if r_chunked:
        daemon_fn = _make_chunk_daemon_fn(r_engine) if r_engine in QWEN_ENGINES else None
        chunk_paths = _clone_chunked(
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

    if r_engine in QWEN_ENGINES:
        daemon_result = _try_daemon(
            {
                "action": "clone",
                "engine": r_engine,
                "text": r_text,
                "voice": None,
                "ref_audio": str(ref_path.resolve()),
                "ref_text": ref_text,
                "output_path": str(out.resolve()),
                "language": r_language,
                "instruct": extra.get("instruct"),
                "exaggeration": extra.get("exaggeration"),
                "cfg_weight": extra.get("cfg_weight"),
                "segment_gap": extra.get("segment_gap"),
                "crossfade": extra.get("crossfade"),
                "segments": [dataclasses.asdict(s) for s in (extra.get("segments") or [])],
            }
        )
        if daemon_result:
            out = daemon_result
            mp3_path = None
            if r_mp3:
                from voicecli.utils import wav_to_mp3

                mp3_path = wav_to_mp3(out)
            return TTSResult(wav_path=out, mp3_path=mp3_path)

    out = eng.clone(r_text, ref_path, out, ref_text=ref_text, language=r_language, **extra)

    mp3_path = None
    if r_mp3:
        from voicecli.utils import wav_to_mp3

        mp3_path = wav_to_mp3(out)

    return TTSResult(wav_path=out, mp3_path=mp3_path)


def transcribe(
    audio: str | Path,
    *,
    model: str = "large-v3-turbo",
    language: str | None = None,
    output: str | Path | None = None,
):
    """Transcribe an audio file to text.

    Args:
        audio: Path to audio file.
        model: Whisper model name.
        language: Force language code.
        output: Save transcription text to file.

    Returns:
        TranscriptionResult with .text, .language, .segments.

    Raises:
        FileNotFoundError: Audio file not found.
        ValueError: Invalid model name.
    """
    from voicecli.transcribe import TranscriptionResult  # noqa: F811
    from voicecli.transcribe import transcribe as _transcribe

    audio_path = Path(audio)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    result: TranscriptionResult = _transcribe(audio_path, model=model, language=language)

    if output is not None:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(result.text, encoding="utf-8")

    return result


def list_engines() -> list[str]:
    """Return available TTS engine names."""
    from voicecli.engine import available_engines

    return available_engines()


def list_voices(engine: str) -> list[str]:
    """Return available voice names for an engine.

    Raises:
        ValueError: If engine name is unknown.
    """
    from voicecli.engine import get_engine

    try:
        eng = get_engine(engine)
    except ValueError:
        raise ValueError(f"Unknown engine '{engine}'. Available: {list_engines()}")
    return eng.list_voices()


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
