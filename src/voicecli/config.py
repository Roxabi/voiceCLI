"""Load user defaults from voicecli.toml."""

from __future__ import annotations

import os
import sys
import tomllib
from typing import Any, Callable
from pathlib import Path


def get_data_dir() -> Path:
    """Resolve the voiceCLI data directory with grandfathered fallback.

    Priority:
      1. ``VOICECLI_DATA_DIR`` environment variable.
      2. ``~/.roxabi/voicecli/`` (new canonical location).
      3. ``~/.voicecli/`` (legacy fallback — only if it exists and the new dir does not).
    """
    env = os.environ.get("VOICECLI_DATA_DIR")
    if env:
        path = Path(env).expanduser()
        if not path.is_absolute():
            raise ValueError(f"VOICECLI_DATA_DIR must be an absolute path, got: {env}")
        path = path.resolve()
        if path.exists() and not path.is_dir():
            raise ValueError(f"VOICECLI_DATA_DIR must be a directory, got: {path}")
        return path
    new_dir = Path.home() / ".roxabi" / "voicecli"
    old_dir = Path.home() / ".voicecli"
    if new_dir.exists() or not old_dir.exists():
        return new_dir
    return old_dir


VOICECLI_DIR = get_data_dir()


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("true", "1", "yes", "on")


_KNOWN_DEFAULTS: dict[str, Callable[..., Any]] = {
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
    "flow_steps": int,
    "cfg_alpha": float,
    "temperature": float,
    "top_p": float,
    "min_p": float,
    "repetition_penalty": float,
    "segment_gap": int,
    "crossfade": int,
    "plain": _parse_bool,
    "chunked": _parse_bool,
    "chunk_size": int,
}


def _find_config() -> Path | None:
    """Look for voicecli.toml in ~/.roxabi/voicecli/ (or ~/.voicecli/ fallback), then walk up from CWD to $HOME."""
    canonical = VOICECLI_DIR / "voicecli.toml"
    if canonical.is_file():
        return canonical
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
    """Load [defaults] from voicecli.toml, checking ~/.roxabi/voicecli/ (or ~/.voicecli/ fallback) then walking up from CWD to $HOME. Returns empty dict if not found.

    Args:
        config: Explicit path to a toml file. If provided, skips the walk-up search.
    """
    path = config if config is not None else _find_config()
    if path is None:
        print(
            f"voicecli: no voicecli.toml found (searched {VOICECLI_DIR} and from CWD to $HOME); using built-in defaults",
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
    "hotkey_cancel": str,
    "hotkey_mode": str,
    "model": str,
    "language": str,
    "language_detection_threshold": float,
    "language_detection_segments": int,
    "language_fallback": str,
    "auto_paste": bool,
}


def load_vocab(vocab: Path | None = None) -> list[str]:
    """Load personal vocabulary from ~/.roxabi/voicecli/voicecli.vocab (or ~/.voicecli/ fallback, or walk-up fallback).

    Returns a list of words/phrases (comments and blank lines stripped).
    Returns an empty list if no file is found.

    Args:
        vocab: Explicit path to a vocab file. If provided, skips the walk-up search.
    """
    if vocab is not None:
        path: Path | None = vocab
    else:
        canonical = VOICECLI_DIR / "voicecli.vocab"
        if canonical.is_file():
            path = canonical
        else:
            home = Path.home().resolve()
            current = Path.cwd().resolve()
            path = None
            while True:
                candidate = current / "voicecli.vocab"
                if candidate.is_file():
                    path = candidate
                    break
                if current == home or current.parent == current:
                    break
                current = current.parent

    if path is None:
        return []
    words = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.append(line)
    return words


def vocab_to_prompt(words: list[str]) -> str | None:
    """Format a vocab list as an initial_prompt fragment, or None if empty."""
    if not words:
        return None
    return ", ".join(words) + "."


def load_stt_config(config: Path | None = None) -> dict:
    """Load the ``[stt]`` table from voicecli.toml.

    Returns ``{"hotkey": "ctrl+space"}`` (and other defaults) when no config is
    found or the table is absent.

    Args:
        config: Explicit path to a toml file. If provided, skips the walk-up search.
    """
    path = config if config is not None else _find_config()
    result: dict[str, object] = {
        "hotkey": "ctrl+space",
        "hotkey_cancel": "alt+shift+esc",
        "hotkey_mode": "alt+shift+tab",
    }
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


_KNOWN_TTS: dict[str, type] = {
    "default_engine": str,
}


def load_tts_config(config: Path | None = None) -> dict:
    """Load the [tts] table from voicecli.toml.

    Returns {"default_engine": None} when no config found.

    Args:
        config: Explicit path to a toml file. If provided, skips the walk-up search.
    """
    path = config if config is not None else _find_config()
    result: dict[str, str | None] = {"default_engine": None}
    if path is None:
        return result
    with open(path, "rb") as f:
        data = tomllib.load(f)
    raw = data.get("tts", {})
    for key, expected_type in _KNOWN_TTS.items():
        if key in raw:
            try:
                result[key] = expected_type(raw[key])
            except (ValueError, TypeError):
                pass
    return result


_KNOWN_NATS: dict[str, type] = {
    "max_cached_engines": int,
}


def load_nats_config(config: Path | None = None) -> dict:
    """Load the ``[nats]`` table from voicecli.toml.

    Returns ``{"max_cached_engines": 2}`` when no config is found or the table
    is absent. Value is clamped to [1, 5].

    Args:
        config: Explicit path to a toml file. If provided, skips the walk-up search.
    """
    path = config if config is not None else _find_config()
    result: dict[str, int] = {"max_cached_engines": 2}
    if path is None:
        return result
    with open(path, "rb") as f:
        data = tomllib.load(f)
    raw = data.get("nats", {})
    for key, expected_type in _KNOWN_NATS.items():
        if key in raw:
            try:
                result[key] = expected_type(raw[key])
            except (ValueError, TypeError):
                pass
    # Clamp max_cached_engines to [1, 5]
    result["max_cached_engines"] = max(1, min(5, result.get("max_cached_engines", 2)))
    return result
