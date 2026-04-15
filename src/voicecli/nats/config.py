"""NATS adapter configuration resolvers (env + toml)."""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

DEFAULT_ENGINE = "qwen-fast"
DEFAULT_MODEL = "large-v3-turbo"


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
    except Exception as e:
        # Broken install, missing config, or unparseable TOML — fall back to the
        # default engine but surface the failure for debugging. A silent pass
        # would mask ImportError / PermissionError as "config absent".
        log.debug("config fallback during _resolve_engine: %s: %s", type(e).__name__, e)
    return DEFAULT_ENGINE


def _resolve_model(cli_value: str | None = None) -> str:
    """Resolve STT model: CLI arg > VOICECLI_MODEL env > voicecli.toml [stt].model > DEFAULT_MODEL."""
    if cli_value:
        return cli_value
    v = os.environ.get("VOICECLI_MODEL")
    if v:
        return v
    try:
        from voicecli.config import load_config

        cfg = load_config()
        toml_model = cfg.get("stt", {}).get("model")
        if toml_model:
            return toml_model
    except Exception as e:
        log.debug("config fallback during _resolve_model: %s: %s", type(e).__name__, e)
    return DEFAULT_MODEL
