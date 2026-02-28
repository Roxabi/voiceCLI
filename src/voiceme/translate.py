"""Translate a universal TTSDocument to an engine-specific one using the capability matrix."""

import re
from copy import deepcopy

from voiceme.markdown import Segment, TTSDocument

# ── Engine capability matrix ────────────────────────────────────────────────

ENGINE_CAPS = {
    "qwen": {
        "instruct": True,
        "segments": True,
        "tags": "to_instruct",  # convert [laugh] → segment with instruct
        "exaggeration": False,
        "cfg_weight": False,
        "language": True,
        "voice": True,
    },
    "chatterbox": {
        "instruct": False,
        "segments": False,
        "tags": "strip",  # remove tags, can't translate
        "exaggeration": True,
        "cfg_weight": True,
        "language": True,
        "voice": False,
    },
    "chatterbox-turbo": {
        "instruct": False,
        "segments": False,
        "tags": "native",  # keep as-is, engine handles them
        "exaggeration": True,
        "cfg_weight": True,
        "language": False,
        "voice": False,
    },
}

# ── Tag → instruct mapping (for Qwen translation) ──────────────────────────

TAG_TO_INSTRUCT_EN = {
    "laugh": "Laughing",
    "chuckle": "Chuckling softly",
    "cough": "Coughing",
    "sigh": "Sighing",
    "gasp": "Gasping in surprise",
    "groan": "Groaning",
    "sniff": "Sniffling",
    "shush": "Whispering, shushing",
    "clear throat": "Clearing throat",
}

TAG_TO_INSTRUCT_FR = {
    "laugh": "En riant",
    "chuckle": "Avec un petit rire doux",
    "cough": "En toussant",
    "sigh": "En soupirant",
    "gasp": "Avec un hoquet de surprise",
    "groan": "En gémissant",
    "sniff": "En reniflant",
    "shush": "En chuchotant, comme pour faire taire",
    "clear throat": "En se raclant la gorge",
}

_TAG_TO_INSTRUCT_BY_LANG = {
    "french": TAG_TO_INSTRUCT_FR,
    "français": TAG_TO_INSTRUCT_FR,
}


def _get_tag_instruct_map(language: str | None) -> dict[str, str]:
    if language:
        return _TAG_TO_INSTRUCT_BY_LANG.get(language.lower(), TAG_TO_INSTRUCT_EN)
    return TAG_TO_INSTRUCT_EN

_TAG_RE = re.compile(r"\[(" + "|".join(re.escape(t) for t in TAG_TO_INSTRUCT_EN) + r")\]")


# ── Helpers ─────────────────────────────────────────────────────────────────


def _strip_tags(text: str) -> str:
    """Remove all paralinguistic [tag] markers from text."""
    return _TAG_RE.sub("", text).strip()


def _split_segment_on_tags(seg: Segment, tag_map: dict[str, str]) -> list[Segment]:
    """Split a segment at each [tag], mapping tags to instruct directives.

    Tags modify the instruct of the text that follows them.
    Input:  "Hello [laugh] world [sigh] goodbye" (instruct="Speak warmly")
    Output: Segment("Hello", "Speak warmly")
            Segment("world", "Laughing")
            Segment("goodbye", "Sighing")
    """
    parts = _TAG_RE.split(seg.text)
    if len(parts) == 1:
        return [seg]

    result: list[Segment] = []
    # parts[0] is text before first tag
    pre = parts[0].strip()
    if pre:
        result.append(Segment(text=pre, instruct=seg.instruct))

    # Remaining parts alternate: tag_name, text, tag_name, text, ...
    for i in range(1, len(parts), 2):
        tag_name = parts[i]
        text = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if text:
            result.append(Segment(text=text, instruct=tag_map[tag_name]))

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
        tag_map = _get_tag_instruct_map(doc.language)
        expanded: list[Segment] = []
        for seg in doc.segments:
            expanded.extend(_split_segment_on_tags(seg, tag_map))
        doc.segments = expanded
        # Rebuild flat text (tags removed since they're now instructs)
        doc.text = " ".join(seg.text for seg in doc.segments) if doc.segments else doc.text

    # ── Instruct ──
    if not caps["instruct"]:
        doc.instruct = None

    # ── Segments ──
    if not caps["segments"]:
        doc.segments = []

    # ── Numeric controls ──
    if not caps["exaggeration"]:
        doc.exaggeration = None
    if not caps["cfg_weight"]:
        doc.cfg_weight = None

    # ── Language / Voice ──
    if not caps["language"]:
        doc.language = None
    if not caps["voice"]:
        doc.voice = None

    return doc
