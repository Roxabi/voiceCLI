"""STT modes — named transcription presets for dictation.

Built-in modes can be overridden or extended in voicecli.toml under [stt.modes.*].
Each mode may specify: language, task, prompt, description (all optional).
"""

from __future__ import annotations

BUILTIN_MODES: dict[str, dict] = {
    "default": {
        "description": "Auto language detection, clean transcription",
        "task": "transcribe",
        "prompt": "Clear speech. Proper punctuation and capitalization.",
    },
    "french": {
        "description": "Dictation in French",
        "language": "fr",
        "task": "transcribe",
        "prompt": "Bonjour. Voici un texte en français avec une ponctuation correcte.",
    },
    "english": {
        "description": "Dictation in English",
        "language": "en",
        "task": "transcribe",
        "prompt": "Hello. Here is a well-punctuated English sentence.",
    },
    "fr-to-en": {
        "description": "Speak French → English output",
        "language": "fr",
        "task": "translate",
    },
    "translate-en": {
        "description": "Speak anything → English output",
        "task": "translate",
    },
    "email": {
        "description": "Email dictation — speak naturally, get structured email",
        "task": "transcribe",
        "prompt": (
            "Email dictation. Subject: [topic]. Dear [Name], [body]. Best regards."
            " Professional tone. Proper punctuation."
        ),
    },
    "code": {
        "description": "Voice to code / technical terms",
        "task": "transcribe",
        "prompt": "Variable name, function name, code comment, no filler words.",
    },
}

_MODE_FIELDS = frozenset({"description", "language", "task", "prompt"})


def load_modes(config: dict) -> dict[str, dict]:
    """Return merged modes dict: built-ins overridden/extended by user [stt.modes.*].

    Args:
        config: Full parsed voicecli.toml as a dict (from load_config()).
    """
    modes: dict[str, dict] = {name: dict(m) for name, m in BUILTIN_MODES.items()}

    user_modes = config.get("stt", {}).get("modes", {})
    for name, raw in user_modes.items():
        if not isinstance(raw, dict):
            continue
        # Filter to known fields only
        filtered = {k: v for k, v in raw.items() if k in _MODE_FIELDS}
        if name in modes:
            modes[name] = {**modes[name], **filtered}
        else:
            modes[name] = filtered

    return modes


def get_mode(name: str, config: dict) -> dict:
    """Return the mode dict for *name*, or raise ValueError if not found.

    Args:
        name: Mode name (e.g. "french", "code").
        config: Full parsed voicecli.toml as a dict (from load_config()).
    """
    modes = load_modes(config)
    if name not in modes:
        available = ", ".join(sorted(modes))
        raise ValueError(f"Unknown STT mode '{name}'. Available: {available}")
    return modes[name]
