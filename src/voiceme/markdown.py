"""Parse markdown files with YAML frontmatter for TTS metadata."""

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Segment:
    """A text segment with its own instruct directive."""

    text: str
    instruct: str | None = None


@dataclass
class TTSDocument:
    text: str
    language: str | None = None
    voice: str | None = None
    engine: str | None = None
    instruct: str | None = None
    exaggeration: float | None = None
    cfg_weight: float | None = None
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


_INSTRUCT_RE = re.compile(r"<!--\s*instruct:\s*(.+?)\s*-->")


def _parse_segments(body: str, default_instruct: str | None) -> list[Segment]:
    """Split body on <!-- instruct: ... --> comments into per-section segments."""
    parts = _INSTRUCT_RE.split(body)

    # No instruct comments found → single segment
    if len(parts) == 1:
        text = strip_markdown(parts[0])
        if text:
            return [Segment(text=text, instruct=default_instruct)]
        return []

    segments: list[Segment] = []

    # parts[0] is text before the first <!-- instruct: --> (may be empty)
    pre_text = strip_markdown(parts[0])
    if pre_text:
        segments.append(Segment(text=pre_text, instruct=default_instruct))

    # Remaining parts alternate: instruct_value, text, instruct_value, text, ...
    for i in range(1, len(parts), 2):
        instruct = parts[i].strip()
        text = strip_markdown(parts[i + 1]) if i + 1 < len(parts) else ""
        if text:
            segments.append(Segment(text=text, instruct=instruct or default_instruct))

    return segments


def parse_md_file(path: Path) -> TTSDocument:
    """Parse a .md file into a TTSDocument."""
    content = path.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(content)

    known_keys = {"language", "voice", "engine", "instruct", "exaggeration", "cfg_weight"}
    extra = {k: v for k, v in metadata.items() if k not in known_keys}

    default_instruct = metadata.get("instruct")

    # Parse numeric fields
    exaggeration = None
    if "exaggeration" in metadata:
        try:
            exaggeration = float(metadata["exaggeration"])
        except ValueError:
            pass

    cfg_weight = None
    if "cfg_weight" in metadata:
        try:
            cfg_weight = float(metadata["cfg_weight"])
        except ValueError:
            pass

    segments = _parse_segments(body, default_instruct)
    # Full text is the concatenation of all segments (for backward compat)
    text = " ".join(seg.text for seg in segments) if segments else strip_markdown(body)

    return TTSDocument(
        text=text,
        language=metadata.get("language"),
        voice=metadata.get("voice"),
        engine=metadata.get("engine"),
        instruct=default_instruct,
        exaggeration=exaggeration,
        cfg_weight=cfg_weight,
        extra=extra,
        segments=segments,
    )
