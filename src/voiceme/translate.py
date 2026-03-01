"""Translate a universal TTSDocument to an engine-specific one using the capability matrix."""

import random
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
        "segments": True,
        "tags": "strip",  # remove tags, can't translate
        "exaggeration": True,
        "cfg_weight": True,
        "language": True,
        "voice": False,
    },
    "chatterbox-turbo": {
        "instruct": False,
        "segments": True,
        "tags": "native",  # keep as-is, engine handles them
        "exaggeration": True,
        "cfg_weight": True,
        "language": False,
        "voice": False,
    },
}
ENGINE_CAPS["qwen-fast"] = ENGINE_CAPS["qwen"]

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

# ── Transition instructs (smooth ramp-in / ramp-out around tags) ─────────────
# BEFORE: original instruct is replaced with "{base}, {transition_before}"
# AFTER:  tag instruct is replaced with "{tag_instruct}, {transition_after}"

TAG_TRANSITION_EN = {
    "laugh": ("building up to a laugh at the end", "then gradually calming down"),
    "chuckle": ("with growing amusement", "then settling back to normal"),
    "cough": ("with a slight throat irritation", "then recovering composure"),
    "sigh": ("with growing weariness", "then slowly regaining composure"),
    "gasp": ("with rising tension", "then catching breath and calming down"),
    "groan": ("with increasing discomfort", "then easing off gradually"),
    "sniff": ("getting slightly emotional", "then pulling yourself together"),
    "shush": ("lowering voice progressively", "then slowly returning to normal volume"),
    "clear throat": ("with slight hesitation", "then speaking more clearly"),
}

TAG_TRANSITION_FR = {
    "laugh": ("de plus en plus amusé, prêt à éclater de rire", "puis retrouve progressivement son calme"),
    "chuckle": ("avec une pointe d'amusement grandissante", "puis reprend un ton normal"),
    "cough": ("avec une légère gêne dans la gorge", "puis retrouve sa voix"),
    "sigh": ("avec une lassitude croissante", "puis reprend doucement contenance"),
    "gasp": ("avec une tension montante", "puis reprend son souffle et se calme"),
    "groan": ("avec un inconfort grandissant", "puis se relâche progressivement"),
    "sniff": ("de plus en plus ému", "puis se reprend doucement"),
    "shush": ("en baissant progressivement la voix", "puis reprend un volume normal"),
    "clear throat": ("avec une légère hésitation", "puis parle plus clairement"),
}

# ── Onomatopoeia pools (per tag, per language) ──────────────────────────────

TAG_ONOMATOPOEIA_EN = {
    "laugh": ["Ha ha ha ha!", "Ah ah ah!", "Hahaha!", "Ha ha!"],
    "chuckle": ["Heh heh.", "Hehe.", "He he he."],
    "cough": ["Ahem!", "Kof kof!", "Ehem!"],
    "sigh": ["Haaa...", "Pfff...", "Hhh..."],
    "gasp": ["Oh!", "Ah!", "Oh my!"],
    "groan": ["Ugh...", "Ngh...", "Mmh..."],
    "sniff": ["Sniff.", "Snf...", "Sniff sniff."],
    "shush": ["Shh...", "Shhh!", "Chh..."],
    "clear throat": ["Ahem.", "Hmm hmm.", "Ehem."],
}

TAG_ONOMATOPOEIA_FR = {
    "laugh": ["Ah ah ah ah !", "Ha ha ha !", "Hahaha !", "Hi hi hi !"],
    "chuckle": ["Hé hé.", "Hi hi.", "Hé hé hé."],
    "cough": ["Hm hm !", "Kof kof !", "Ahem !"],
    "sigh": ["Haaa...", "Pfff...", "Ohh..."],
    "gasp": ["Oh !", "Ah !", "Oh là là !"],
    "groan": ["Aïe...", "Ngh...", "Mmh..."],
    "sniff": ["Snif.", "Snif snif.", "Snif snif snif."],
    "shush": ["Chut...", "Chh...", "Chhh !"],
    "clear throat": ["Hm hm.", "Ahem.", "Hmm."],
}

# ── Unified per-language tag data registry ───────────────────────────────────
# Single lookup point for all tag-related data, keyed by ISO 639-1 code.

_TAG_DATA_EN = {
    "instruct": TAG_TO_INSTRUCT_EN,
    "transition": TAG_TRANSITION_EN,
    "onomatopoeia": TAG_ONOMATOPOEIA_EN,
}

_TAG_DATA_BY_LANG = {
    "fr": {
        "instruct": TAG_TO_INSTRUCT_FR,
        "transition": TAG_TRANSITION_FR,
        "onomatopoeia": TAG_ONOMATOPOEIA_FR,
    },
}

# Validate all locales have the same tag keys as English
for _lang, _pools in _TAG_DATA_BY_LANG.items():
    for _pool_name, _pool in _pools.items():
        assert _pool.keys() == _TAG_DATA_EN[_pool_name].keys(), \
            f"Tag {_pool_name} keys mismatch for {_lang}"


def _resolve_tag_pool(pool_name: str, language: str | None) -> dict:
    """Resolve a tag data pool by name and language, falling back to English."""
    from voiceme.utils import resolve_language
    lang_code = resolve_language(language) if language else "en"
    lang_data = _TAG_DATA_BY_LANG.get(lang_code, _TAG_DATA_EN)
    return lang_data[pool_name]

_TAG_RE = re.compile(r"\[(" + "|".join(re.escape(t) for t in TAG_TO_INSTRUCT_EN) + r")\]")


# ── Helpers ─────────────────────────────────────────────────────────────────


def _strip_tags(text: str) -> str:
    """Remove all paralinguistic [tag] markers from text."""
    return _TAG_RE.sub("", text).strip()


def _split_segment_on_tags(
    seg: Segment, tag_map: dict[str, str], language: str | None = None,
) -> list[Segment]:
    """Split a segment at each [tag] with smooth transitions.

    - Text BEFORE a tag: original instruct + ramp-up transition
    - Text AFTER a tag: onomatopoeia + text, tag instruct + ramp-down transition

    Input:  "Hello [laugh] world" (instruct="Calm")
    Output: Segment("Hello", "Calm, building up to a laugh at the end")
            Segment("Ha ha! world", "Laughing, then gradually calming down")
    """
    parts = _TAG_RE.split(seg.text)
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
