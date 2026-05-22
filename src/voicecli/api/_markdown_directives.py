"""Directive parsing internals for markdown TTS scripts.

Handles HTML comment directives (<!-- key: value -->), segment splitting,
and frontmatter-to-TTSDocument conversion.

Private module — import via voicecli.markdown, not directly.
"""

import re
from pathlib import Path

from voicecli.api.markdown import (
    TTSDocument,
    Segment,
    _INSTRUCT_PARTS,
    compose_instruct,
    parse_frontmatter,
    strip_markdown,
)


_COMMENT_RE = re.compile(r"<!--(.+?)-->", re.DOTALL)

_STR_DIRECTIVES = {"instruct", "accent", "personality", "speed", "emotion", "language", "voice"}
_FLOAT_DIRECTIVES = {
    "exaggeration",
    "cfg_weight",
    "cfg_alpha",
    "temperature",
    "top_p",
    "min_p",
    "repetition_penalty",
}
_INT_DIRECTIVES = {"segment_gap", "crossfade", "flow_steps"}
_ALL_DIRECTIVES = _STR_DIRECTIVES | _FLOAT_DIRECTIVES | _INT_DIRECTIVES


def _parse_comment_kvs(content: str) -> dict[str, str]:
    """Parse comma-separated key: value pairs from an HTML comment body.

    Handles quoted values (with commas inside), unquoted values, single/double quotes.
    Example: 'emotion: "Passionnée, mais contenue", speed: "Rapide"' →
             {"emotion": "Passionnée, mais contenue", "speed": "Rapide"}
    """
    result: dict[str, str] = {}
    content = content.strip()
    i = 0
    n = len(content)

    while i < n:
        # Skip whitespace and commas between pairs
        while i < n and content[i] in (" ", "\t", "\n", "\r", ","):
            i += 1
        if i >= n:
            break

        # Parse key (word chars until colon)
        key_start = i
        while i < n and content[i] not in (":", " ", "\t", "\n"):
            i += 1
        key = content[key_start:i].strip()
        if not key:
            break

        # Skip to colon
        while i < n and content[i] in (" ", "\t"):
            i += 1
        if i >= n or content[i] != ":":
            break
        i += 1  # skip colon

        # Skip whitespace after colon
        while i < n and content[i] in (" ", "\t"):
            i += 1
        if i >= n:
            break

        # Parse value — quoted or unquoted
        if content[i] in ('"', "'"):
            quote = content[i]
            i += 1  # skip opening quote
            val_start = i
            while i < n and content[i] != quote:
                i += 1
            value = content[val_start:i]
            if i < n:
                i += 1  # skip closing quote
        else:
            # Unquoted: read until comma or end
            val_start = i
            while i < n and content[i] != ",":
                i += 1
            value = content[val_start:i].strip()

        result[key] = value

    return result


def _parse_directive_value(key: str, raw: str) -> object:
    """Parse a directive value to its expected type."""
    raw = raw.strip()
    # Strip surrounding quotes for string values
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        raw = raw[1:-1]
    if key in _FLOAT_DIRECTIVES:
        return float(raw)
    if key in _INT_DIRECTIVES:
        return int(raw)
    return raw


def _compose_segment_instruct(seg: Segment, pending_overrides: dict) -> None:
    """Compose instruct from structured parts if appropriate.

    Rules:
    - If instruct was explicitly set via inline directive → keep as-is (bypass)
    - If any structured part was overridden at this section level → compose from all parts
    - Otherwise, compose from inherited parts if no inherited instruct
    """
    parts_overridden = any(k in pending_overrides for k in _INSTRUCT_PARTS)
    if parts_overridden:
        composed = compose_instruct(seg.accent, seg.personality, seg.speed, seg.emotion)
        if composed:
            seg.instruct = composed
    elif "instruct" not in pending_overrides and seg.instruct is None:
        composed = compose_instruct(seg.accent, seg.personality, seg.speed, seg.emotion)
        if composed:
            seg.instruct = composed


def _parse_segments(body: str, defaults: dict) -> list[Segment]:
    """Split body on <!-- key: value --> directives into per-section segments.

    Each section inherits from frontmatter defaults, overridden by inline directives.
    Consecutive directives accumulate and apply to the text that follows.
    """
    matches = list(_COMMENT_RE.finditer(body))

    if not matches:
        text = strip_markdown(body)
        if text:
            seg = Segment(text=text, **{k: v for k, v in defaults.items() if k != "text"})
            _compose_segment_instruct(seg, {})
            return [seg]
        return []

    segments: list[Segment] = []
    pending_overrides: dict = {}
    prev_end = 0

    for match in matches:
        text_before = body[prev_end : match.start()]
        stripped = strip_markdown(text_before)
        if stripped:
            seg_kwargs = {**defaults, **pending_overrides, "text": stripped}
            seg = Segment(**seg_kwargs)
            _compose_segment_instruct(seg, pending_overrides)
            segments.append(seg)
            pending_overrides = {}

        kvs = _parse_comment_kvs(match.group(1))
        for key, raw_value in kvs.items():
            if key in _ALL_DIRECTIVES:
                try:
                    pending_overrides[key] = _parse_directive_value(key, raw_value)
                except (ValueError, TypeError):
                    pass

        prev_end = match.end()

    remaining = strip_markdown(body[prev_end:])
    if remaining:
        seg_kwargs = {**defaults, **pending_overrides, "text": remaining}
        seg = Segment(**seg_kwargs)
        _compose_segment_instruct(seg, pending_overrides)
        segments.append(seg)

    return segments


def _opt_float(metadata: dict, key: str) -> float | None:
    try:
        return float(metadata[key]) if key in metadata else None
    except ValueError:
        return None


def _opt_int(metadata: dict, key: str) -> int | None:
    try:
        return int(metadata[key]) if key in metadata else None
    except ValueError:
        return None


_NUMERIC_FLOAT_KEYS = (
    "exaggeration",
    "cfg_weight",
    "cfg_alpha",
    "temperature",
    "top_p",
    "min_p",
    "repetition_penalty",
)
_NUMERIC_INT_KEYS = ("flow_steps", "segment_gap", "crossfade")


def _build_seg_defaults(metadata: dict) -> tuple[dict, dict]:
    """Build (seg_defaults, numeric_vals) from frontmatter metadata."""
    numeric_vals: dict = {k: _opt_float(metadata, k) for k in _NUMERIC_FLOAT_KEYS}
    numeric_vals.update({k: _opt_int(metadata, k) for k in _NUMERIC_INT_KEYS})

    seg_defaults: dict = {}
    if metadata.get("instruct"):
        seg_defaults["instruct"] = metadata["instruct"]
    for part in _INSTRUCT_PARTS:
        if metadata.get(part):
            seg_defaults[part] = metadata[part]
    if metadata.get("language"):
        seg_defaults["language"] = metadata["language"]
    if metadata.get("voice"):
        seg_defaults["voice"] = metadata["voice"]
    for k, v in numeric_vals.items():
        if v is not None:
            seg_defaults[k] = v

    return seg_defaults, numeric_vals


_KNOWN_KEYS = {
    "language",
    "voice",
    "engine",
    "instruct",
    "accent",
    "personality",
    "speed",
    "emotion",
    "exaggeration",
    "cfg_weight",
    "flow_steps",
    "cfg_alpha",
    "temperature",
    "top_p",
    "min_p",
    "repetition_penalty",
    "segment_gap",
    "crossfade",
}


def parse_md_file(path: Path) -> TTSDocument:
    """Parse a .md file into a TTSDocument."""
    content = path.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(content)

    extra = {k: v for k, v in metadata.items() if k not in _KNOWN_KEYS}
    seg_defaults, numeric_vals = _build_seg_defaults(metadata)
    segments = _parse_segments(body, seg_defaults)
    text = " ".join(seg.text for seg in segments) if segments else strip_markdown(body)

    doc_instruct = metadata.get("instruct")
    if doc_instruct is None:
        doc_instruct = compose_instruct(
            metadata.get("accent"),
            metadata.get("personality"),
            metadata.get("speed"),
            metadata.get("emotion"),
        )

    return TTSDocument(
        text=text,
        language=metadata.get("language"),
        voice=metadata.get("voice"),
        engine=metadata.get("engine"),
        instruct=doc_instruct,
        accent=metadata.get("accent"),
        personality=metadata.get("personality"),
        speed=metadata.get("speed"),
        emotion=metadata.get("emotion"),
        exaggeration=numeric_vals["exaggeration"],
        cfg_weight=numeric_vals["cfg_weight"],
        flow_steps=numeric_vals["flow_steps"],
        cfg_alpha=numeric_vals["cfg_alpha"],
        temperature=numeric_vals["temperature"],
        top_p=numeric_vals["top_p"],
        min_p=numeric_vals["min_p"],
        repetition_penalty=numeric_vals["repetition_penalty"],
        segment_gap=numeric_vals["segment_gap"],
        crossfade=numeric_vals["crossfade"],
        extra=extra,
        segments=segments,
    )
