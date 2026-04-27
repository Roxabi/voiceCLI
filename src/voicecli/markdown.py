"""Parse markdown files with YAML frontmatter for TTS metadata."""

import re
from dataclasses import dataclass, field
from pathlib import Path


_INSTRUCT_PARTS = ("accent", "personality", "speed", "emotion")


def compose_instruct(
    accent: str | None = None,
    personality: str | None = None,
    speed: str | None = None,
    emotion: str | None = None,
) -> str | None:
    """Join non-None instruct parts into a single instruct string."""
    parts = [p for p in (accent, personality, speed, emotion) if p]
    return ". ".join(parts) if parts else None


@dataclass
class Segment:
    """A text segment with per-section overrides."""

    text: str
    instruct: str | None = None
    accent: str | None = None
    personality: str | None = None
    speed: str | None = None
    emotion: str | None = None
    exaggeration: float | None = None
    cfg_weight: float | None = None
    flow_steps: int | None = None
    cfg_alpha: float | None = None
    temperature: float | None = None
    top_p: float | None = None
    min_p: float | None = None
    repetition_penalty: float | None = None
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
    accent: str | None = None
    personality: str | None = None
    speed: str | None = None
    emotion: str | None = None
    exaggeration: float | None = None
    cfg_weight: float | None = None
    flow_steps: int | None = None
    cfg_alpha: float | None = None
    temperature: float | None = None
    top_p: float | None = None
    min_p: float | None = None
    repetition_penalty: float | None = None
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
    # Strip before newline conversion to avoid leading ". " from frontmatter gap
    text = text.strip()
    # Join paragraphs: collapse multiple newlines into ". " for natural pausing
    text = re.sub(r"\n{2,}", ". ", text)
    # Single newlines → space
    text = re.sub(r"\n", " ", text)
    # Clean up multiple spaces/periods
    text = re.sub(r"\.\s*\.", ".", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def parse_md_file(path: Path) -> TTSDocument:
    """Parse a .md file into a TTSDocument."""
    # Deferred import avoids circular dependency:
    # _markdown_directives imports from this module at its module level,
    # which is safe here because markdown.py is fully initialised before
    # parse_md_file() is ever called.
    from voicecli._markdown_directives import parse_md_file as _parse  # type: ignore[import-not-found]

    return _parse(path)


def _parse_comment_kvs(content: str) -> dict[str, str]:
    """Re-export from _markdown_directives (used by tests and any external callers)."""
    from voicecli._markdown_directives import _parse_comment_kvs as _fn  # type: ignore[import-not-found]

    return _fn(content)


def _parse_segments(body: str, defaults: dict) -> list[Segment]:
    """Re-export from _markdown_directives (used by tests and any external callers)."""
    from voicecli._markdown_directives import _parse_segments as _fn  # type: ignore[import-not-found]

    return _fn(body, defaults)
