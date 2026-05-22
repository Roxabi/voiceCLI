"""Translate a universal TTSDocument to an engine-specific one using the capability matrix."""

import random
from copy import deepcopy

from voicecli.api.engine_caps import (
    ENGINE_CAPS,
    TAG_DATA_BY_LANG,
    TAG_DATA_EN,
    TAG_RE,
)
from voicecli.api.markdown import Segment, TTSDocument

__all__ = ["ENGINE_CAPS", "translate_for_engine", "_strip_tags"]


# ── Helpers ─────────────────────────────────────────────────────────────────


def _resolve_tag_pool(pool_name: str, language: str | None) -> dict:
    """Resolve a tag data pool by name and language, falling back to English."""
    from voicecli.core.utils import resolve_language

    lang_code = resolve_language(language) if language else "en"
    lang_data = TAG_DATA_BY_LANG.get(lang_code, TAG_DATA_EN)
    return lang_data[pool_name]


def _strip_tags(text: str) -> str:
    """Remove all paralinguistic [tag] markers from text."""
    return TAG_RE.sub("", text).strip()


def _split_segment_on_tags(
    seg: Segment,
    tag_map: dict[str, str],
    language: str | None = None,
) -> list[Segment]:
    """Split a segment at each [tag] with smooth transitions.

    - Text BEFORE a tag: original instruct + ramp-up transition
    - Text AFTER a tag: onomatopoeia + text, tag instruct + ramp-down transition

    Input:  "Hello [laugh] world" (instruct="Calm")
    Output: Segment("Hello", "Calm, building up to a laugh at the end")
            Segment("Ha ha! world", "Laughing, then gradually calming down")
    """
    parts = TAG_RE.split(seg.text)
    if len(parts) == 1:
        return [seg]

    result: list[Segment] = []
    base_instruct = seg.instruct or ""

    # parts[0] is text before first tag
    pre = parts[0].strip()
    if pre:
        # First tag determines the ramp-up for the preceding text
        first_tag = parts[1]
        ramp_up, _ = _resolve_tag_pool("transition", language)[first_tag]
        instruct = f"{base_instruct}, {ramp_up}" if base_instruct else ramp_up
        result.append(Segment(text=pre, instruct=instruct))

    # Remaining parts alternate: tag_name, text, tag_name, text, ...
    transition_map = _resolve_tag_pool("transition", language)
    onomatopoeia_map = _resolve_tag_pool("onomatopoeia", language)
    for i in range(1, len(parts), 2):
        tag_name = parts[i]
        text = parts[i + 1].strip() if i + 1 < len(parts) else ""
        filler = random.choice(onomatopoeia_map[tag_name])
        merged = f"{filler} {text}".strip() if text else filler

        _, ramp_down = transition_map[tag_name]
        tag_instruct = tag_map[tag_name]
        instruct = (
            f"{base_instruct}, {tag_instruct}, {ramp_down}"
            if base_instruct
            else f"{tag_instruct}, {ramp_down}"
        )
        result.append(Segment(text=merged, instruct=instruct))

    return result


# ── Main translator ────────────────────────────────────────────────────────


def translate_for_engine(doc: TTSDocument, engine: str) -> TTSDocument:
    """Adapt a universal TTSDocument to a specific engine's capabilities."""
    caps = ENGINE_CAPS.get(engine)
    if caps is None:
        return doc  # unknown engine, pass through unchanged

    doc = deepcopy(doc)

    # ── Tags ──
    tag_mode = caps["tags"]
    if tag_mode == "strip":
        doc.text = _strip_tags(doc.text)
        for seg in doc.segments:
            seg.text = _strip_tags(seg.text)
    elif tag_mode == "to_instruct":
        tag_map = _resolve_tag_pool("instruct", doc.language)
        expanded: list[Segment] = []
        for seg in doc.segments:
            expanded.extend(_split_segment_on_tags(seg, tag_map, doc.language))
        doc.segments = expanded
        # Rebuild flat text (tags removed since they're now instructs)
        doc.text = " ".join(seg.text for seg in doc.segments) if doc.segments else doc.text

    # ── Instruct ──
    if not caps["instruct"]:
        doc.instruct = None
        doc.accent = None
        doc.personality = None
        doc.speed = None
        doc.emotion = None
        for seg in doc.segments:
            seg.instruct = None
            seg.accent = None
            seg.personality = None
            seg.speed = None
            seg.emotion = None

    # ── Segments ──
    if not caps["segments"]:
        doc.segments = []

    # ── Numeric controls ──
    if not caps["exaggeration"]:
        doc.exaggeration = None
        for seg in doc.segments:
            seg.exaggeration = None
    if not caps["cfg_weight"]:
        doc.cfg_weight = None
        for seg in doc.segments:
            seg.cfg_weight = None
    if not caps["flow_steps"]:
        doc.flow_steps = None
        for seg in doc.segments:
            seg.flow_steps = None
    if not caps["cfg_alpha"]:
        doc.cfg_alpha = None
        for seg in doc.segments:
            seg.cfg_alpha = None
    if not caps["temperature"]:
        doc.temperature = None
        for seg in doc.segments:
            seg.temperature = None
    if not caps["top_p"]:
        doc.top_p = None
        for seg in doc.segments:
            seg.top_p = None
    if not caps["min_p"]:
        doc.min_p = None
        for seg in doc.segments:
            seg.min_p = None
    if not caps["repetition_penalty"]:
        doc.repetition_penalty = None
        for seg in doc.segments:
            seg.repetition_penalty = None

    # ── Language / Voice ──
    if not caps["language"]:
        doc.language = None
        for seg in doc.segments:
            seg.language = None
    if not caps["voice"]:
        doc.voice = None
        for seg in doc.segments:
            seg.voice = None

    return doc
