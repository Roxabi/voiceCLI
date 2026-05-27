"""Engine capability matrix and tag data tables for translate.py."""

import re

# ── Engine capability matrix ────────────────────────────────────────────────

ENGINE_CAPS: dict[str, dict[str, object]] = {
    "qwen": {
        "instruct": True,
        "segments": True,
        "tags": "to_instruct",  # convert [laugh] → segment with instruct
        "exaggeration": False,
        "cfg_weight": False,
        "flow_steps": False,
        "cfg_alpha": False,
        "temperature": True,
        "top_p": True,
        "min_p": False,
        "repetition_penalty": True,
        "language": True,
        "voice": True,
    },
    "chatterbox": {
        "instruct": False,
        "segments": True,
        "tags": "strip",  # remove tags, can't translate
        "exaggeration": True,
        "cfg_weight": True,
        "flow_steps": False,
        "cfg_alpha": False,
        "temperature": True,
        "top_p": True,
        "min_p": True,
        "repetition_penalty": True,
        "language": True,
        "voice": False,
    },
    "chatterbox-turbo": {
        "instruct": False,
        "segments": True,
        "tags": "native",  # keep as-is, engine handles them
        "exaggeration": True,
        "cfg_weight": True,
        "flow_steps": False,
        "cfg_alpha": False,
        "temperature": True,
        "top_p": True,
        "min_p": True,
        "repetition_penalty": True,
        "language": False,
        "voice": False,
    },
    "voxtral": {
        "instruct": False,
        "segments": True,
        "tags": "strip",  # no tag support
        "exaggeration": False,
        "cfg_weight": False,
        "flow_steps": True,
        "cfg_alpha": True,
        "temperature": False,
        "top_p": False,
        "min_p": False,
        "repetition_penalty": False,
        "language": True,
        "voice": True,
    },
}
ENGINE_CAPS["qwen-fast"] = ENGINE_CAPS["qwen"]

# ── Tag → instruct mapping (for Qwen translation) ──────────────────────────

TAG_TO_INSTRUCT_EN: dict[str, str] = {
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

TAG_TO_INSTRUCT_FR: dict[str, str] = {
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

TAG_TRANSITION_EN: dict[str, tuple[str, str]] = {
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

TAG_TRANSITION_FR: dict[str, tuple[str, str]] = {
    "laugh": (
        "de plus en plus amusé, prêt à éclater de rire",
        "puis retrouve progressivement son calme",
    ),
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

TAG_ONOMATOPOEIA_EN: dict[str, list[str]] = {
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

TAG_ONOMATOPOEIA_FR: dict[str, list[str]] = {
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

TAG_DATA_EN: dict[str, dict] = {
    "instruct": TAG_TO_INSTRUCT_EN,
    "transition": TAG_TRANSITION_EN,
    "onomatopoeia": TAG_ONOMATOPOEIA_EN,
}

TAG_DATA_BY_LANG: dict[str, dict[str, dict]] = {
    "fr": {
        "instruct": TAG_TO_INSTRUCT_FR,
        "transition": TAG_TRANSITION_FR,
        "onomatopoeia": TAG_ONOMATOPOEIA_FR,
    },
}

# Validate all locales have the same tag keys as English
for _lang, _pools in TAG_DATA_BY_LANG.items():
    for _pool_name, _pool in _pools.items():
        assert _pool.keys() == TAG_DATA_EN[_pool_name].keys(), (
            f"Tag {_pool_name} keys mismatch for {_lang}"
        )

# Pre-compiled regex matching any known tag name
TAG_RE = re.compile(r"\[(" + "|".join(re.escape(t) for t in TAG_TO_INSTRUCT_EN) + r")\]")
