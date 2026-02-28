"""Parse markdown files with YAML frontmatter for TTS metadata."""

import re
from dataclasses import dataclass, field
from pathlib import Path


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


def parse_md_file(path: Path) -> TTSDocument:
    """Parse a .md file into a TTSDocument."""
    content = path.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(content)
    text = strip_markdown(body)

    known_keys = {"language", "voice", "engine", "instruct", "exaggeration", "cfg_weight"}
    extra = {k: v for k, v in metadata.items() if k not in known_keys}

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

    return TTSDocument(
        text=text,
        language=metadata.get("language"),
        voice=metadata.get("voice"),
        engine=metadata.get("engine"),
        instruct=metadata.get("instruct"),
        exaggeration=exaggeration,
        cfg_weight=cfg_weight,
        extra=extra,
    )
