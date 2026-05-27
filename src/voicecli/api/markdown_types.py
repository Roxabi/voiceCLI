"""Shared primitives for markdown TTS parsing.

Holds the data types and pure helpers consumed by both ``voicecli.markdown``
(public API) and ``voicecli._markdown_directives`` (internal directive parser).

Lives in its own layer so neither side needs to import the other to access
``Segment``, ``TTSDocument``, frontmatter parsing, or markdown stripping.
"""

import re
from dataclasses import dataclass, field


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
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        metadata[key] = value

    return metadata, body


def strip_markdown(text: str) -> str:
    """Strip markdown formatting to plain text, preserving paralinguistic tags like [laugh]."""
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = text.strip()
    text = re.sub(r"\n{2,}", ". ", text)
    text = re.sub(r"\n", " ", text)
    text = re.sub(r"\.\s*\.", ".", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()
