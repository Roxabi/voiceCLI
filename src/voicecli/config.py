"""Load user defaults from voicecli.toml."""

import tomllib
from pathlib import Path

def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("true", "1", "yes", "on")


_KNOWN_DEFAULTS: dict[str, object] = {
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
    "plain": _parse_bool,
    "chunked": _parse_bool,
    "chunk_size": int,
}


def load_defaults() -> dict:
    """Load [defaults] from voicecli.toml in CWD. Returns empty dict if not found."""
    path = Path("voicecli.toml")
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
