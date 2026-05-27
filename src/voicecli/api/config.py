"""API config resolution — voicecli.toml layering + instruct composition."""

from __future__ import annotations

from pathlib import Path


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
