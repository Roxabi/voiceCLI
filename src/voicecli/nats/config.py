"""NATS adapter configuration resolvers (env + toml)."""

from __future__ import annotations

import os

DEFAULT_ENGINE = "qwen-fast"


def _resolve_engine(cli_value: str | None = None) -> str:
    """Resolve TTS engine: CLI arg > VOICECLI_ENGINE > LYRA_TTS_ENGINE > voicecli.toml > DEFAULT_ENGINE."""
    if cli_value:
        return cli_value
    for env_var in ("VOICECLI_ENGINE", "LYRA_TTS_ENGINE"):
        v = os.environ.get(env_var)
        if v:
            return v
    try:
        from voicecli.config import load_config

        cfg = load_config()
        toml_engine = cfg.get("defaults", {}).get("engine")
        if toml_engine:
            return toml_engine
    except Exception:
        pass
    return DEFAULT_ENGINE
