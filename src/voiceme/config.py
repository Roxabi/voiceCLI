"""Load user defaults from voiceme.toml."""

import tomllib
from pathlib import Path

_KNOWN_DEFAULTS: dict[str, type] = {
    "engine": str,
    "language": str,
    "voice": str,
    "instruct": str,
    "accent": str,
    "personality": str,
    "speed": str,
    "emotion": str,
    "exaggeration": float,
    "cfg_weight": float,
    "segment_gap": int,
    "crossfade": int,
}


def load_defaults() -> dict:
    """Load [defaults] from voiceme.toml in CWD. Returns empty dict if not found."""
    path = Path("voiceme.toml")
    if not path.is_file():
        return {}
    with open(path, "rb") as f:
        data = tomllib.load(f)
    raw = data.get("defaults", {})
    result = {}
    for key, expected_type in _KNOWN_DEFAULTS.items():
        if key in raw:
            try:
                result[key] = expected_type(raw[key])
            except (ValueError, TypeError):
                pass
    return result
