"""Load user defaults from voicecli.toml."""

from __future__ import annotations

import sys
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


def _find_config() -> Path | None:
    """Walk up from CWD to $HOME looking for voicecli.toml."""
    home = Path.home().resolve()
    current = Path.cwd().resolve()
    while True:
        candidate = current / "voicecli.toml"
        if candidate.is_file():
            return candidate
        if current == home or current.parent == current:
            return None
        current = current.parent


def load_defaults(config: Path | None = None) -> dict:
    """Load [defaults] from voicecli.toml, walking up from CWD to $HOME. Returns empty dict if not found.

    Args:
        config: Explicit path to a toml file. If provided, skips the walk-up search.
    """
    path = config if config is not None else _find_config()
    if path is None:
        print(
            "voicecli: no voicecli.toml found (searched from CWD to $HOME); using built-in defaults",
            file=sys.stderr,
        )
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


def load_config(config: Path | None = None) -> dict:
    """Load the full voicecli.toml as a raw dict (all tables preserved).

    Returns an empty dict if no config file is found (no warning printed).
    Used by commands that need access to non-[defaults] tables (e.g. [stt]).

    Args:
        config: Explicit path to a toml file. If provided, skips the walk-up search.
    """
    path = config if config is not None else _find_config()
    if path is None:
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


_KNOWN_STT: dict[str, type] = {
    "hotkey": str,
    "model": str,
}


def load_stt_config(config: Path | None = None) -> dict:
    """Load the ``[stt]`` table from voicecli.toml.

    Returns ``{"hotkey": "alt+space"}`` (and other defaults) when no config is
    found or the table is absent.

    Args:
        config: Explicit path to a toml file. If provided, skips the walk-up search.
    """
    path = config if config is not None else _find_config()
    result: dict[str, object] = {"hotkey": "alt+space"}
    if path is None:
        return result
    with open(path, "rb") as f:
        data = tomllib.load(f)
    raw = data.get("stt", {})
    for key, expected_type in _KNOWN_STT.items():
        if key in raw:
            try:
                result[key] = expected_type(raw[key])
            except (ValueError, TypeError):
                pass
    return result
