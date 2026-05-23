"""API input resolution — file detection, markdown parsing, ref resolution."""

from __future__ import annotations

from pathlib import Path


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
        from voicecli.api.config import _apply_config_defaults

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
