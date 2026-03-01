"""Parse markdown files with YAML frontmatter for TTS metadata."""

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Segment:
    """A text segment with per-section overrides."""

    text: str
    instruct: str | None = None
    exaggeration: float | None = None
    cfg_weight: float | None = None
    segment_gap: int | None = None
    crossfade: int | None = None
    language: str | None = None
    voice: str | None = None


@dataclass
class TTSDocument:
    text: str
    language: str | None = None
    voice: str | None = None
    engine: str | None = None
    instruct: str | None = None
    exaggeration: float | None = None
    cfg_weight: float | None = None
    segment_gap: int | None = None
    crossfade: int | None = None
    extra: dict = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Split YAML frontmatter from body. Returns (metadata, body)."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
    if not match:
        return {}, content

    yaml_block, body = match.group(1), match.group(2)

    # Simple YAML parser — handles key: value lines (no nested structures needed)
    metadata = {}
    for line in yaml_block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        metadata[key] = value

    return metadata, body


def strip_markdown(text: str) -> str:
    """Strip markdown formatting to plain text, preserving paralinguistic tags like [laugh]."""
    # Remove headers (# ... )
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove bold/italic markers
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)
    # Remove links [text](url) → text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Remove inline code backticks
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Remove blockquote markers
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    # Remove horizontal rules
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    # Remove images ![alt](url)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    # Join paragraphs: collapse multiple newlines into ". " for natural pausing
    text = re.sub(r"\n{2,}", ". ", text)
    # Single newlines → space
    text = re.sub(r"\n", " ", text)
    # Clean up multiple spaces/periods
    text = re.sub(r"\.\s*\.", ".", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


_DIRECTIVE_RE = re.compile(r"<!--\s*(\w+):\s*(.+?)\s*-->")

# Fields that can appear as <!-- key: value --> inline directives
_STR_DIRECTIVES = {"instruct", "language", "voice"}
_FLOAT_DIRECTIVES = {"exaggeration", "cfg_weight"}
_INT_DIRECTIVES = {"segment_gap", "crossfade"}
_ALL_DIRECTIVES = _STR_DIRECTIVES | _FLOAT_DIRECTIVES | _INT_DIRECTIVES


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


def _parse_segments(body: str, defaults: dict) -> list[Segment]:
    """Split body on <!-- key: value --> directives into per-section segments.

    Each section inherits from frontmatter defaults, overridden by inline directives.
    Consecutive directives accumulate and apply to the text that follows.
    """
    # Find all directives and their positions
    matches = list(_DIRECTIVE_RE.finditer(body))

    # No directives found → single segment with defaults
    if not matches:
        text = strip_markdown(body)
        if text:
            return [Segment(text=text, **{k: v for k, v in defaults.items() if k != "text"})]
        return []

    segments: list[Segment] = []
    pending_overrides: dict = {}
    prev_end = 0

    for match in matches:
        # Text between previous position and this directive
        text_before = body[prev_end:match.start()]
        stripped = strip_markdown(text_before)
        if stripped:
            seg_kwargs = {**defaults, **pending_overrides, "text": stripped}
            segments.append(Segment(**seg_kwargs))
            pending_overrides = {}

        # Accumulate this directive
        key, raw_value = match.group(1), match.group(2)
        if key in _ALL_DIRECTIVES:
            try:
                pending_overrides[key] = _parse_directive_value(key, raw_value)
            except (ValueError, TypeError):
                pass

        prev_end = match.end()

    # Remaining text after last directive
    remaining = strip_markdown(body[prev_end:])
    if remaining:
        seg_kwargs = {**defaults, **pending_overrides, "text": remaining}
        segments.append(Segment(**seg_kwargs))

    return segments


def _parse_optional_float(metadata: dict, key: str) -> float | None:
    if key in metadata:
        try:
            return float(metadata[key])
        except ValueError:
            pass
    return None


def _parse_optional_int(metadata: dict, key: str) -> int | None:
    if key in metadata:
        try:
            return int(metadata[key])
        except ValueError:
            pass
    return None


def parse_md_file(path: Path) -> TTSDocument:
    """Parse a .md file into a TTSDocument."""
    content = path.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(content)

    known_keys = {
        "language", "voice", "engine", "instruct",
        "exaggeration", "cfg_weight", "segment_gap", "crossfade",
    }
    extra = {k: v for k, v in metadata.items() if k not in known_keys}

    exaggeration = _parse_optional_float(metadata, "exaggeration")
    cfg_weight = _parse_optional_float(metadata, "cfg_weight")
    segment_gap = _parse_optional_int(metadata, "segment_gap")
    crossfade = _parse_optional_int(metadata, "crossfade")

    # Defaults that segments inherit from frontmatter
    seg_defaults: dict = {}
    if metadata.get("instruct"):
        seg_defaults["instruct"] = metadata["instruct"]
    if metadata.get("language"):
        seg_defaults["language"] = metadata["language"]
    if metadata.get("voice"):
        seg_defaults["voice"] = metadata["voice"]
    if exaggeration is not None:
        seg_defaults["exaggeration"] = exaggeration
    if cfg_weight is not None:
        seg_defaults["cfg_weight"] = cfg_weight
    if segment_gap is not None:
        seg_defaults["segment_gap"] = segment_gap
    if crossfade is not None:
        seg_defaults["crossfade"] = crossfade

    segments = _parse_segments(body, seg_defaults)
    text = " ".join(seg.text for seg in segments) if segments else strip_markdown(body)

    return TTSDocument(
        text=text,
        language=metadata.get("language"),
        voice=metadata.get("voice"),
        engine=metadata.get("engine"),
        instruct=metadata.get("instruct"),
        exaggeration=exaggeration,
        cfg_weight=cfg_weight,
        segment_gap=segment_gap,
        crossfade=crossfade,
        extra=extra,
        segments=segments,
    )
