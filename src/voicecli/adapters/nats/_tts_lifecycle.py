"""TTS lifecycle list/status handlers."""

from __future__ import annotations

from voicecli.adapters.nats.config import _resolve_engine
from voicecli.core.config import load_nats_config
from voicecli.core.sample_catalog import catalog_revision, list_catalog_entries, load_catalog


def build_tts_list_data(*, default_engine: str) -> dict:
    from voicecli.api.engine_caps import ENGINE_CAPS  # noqa: PLC0415
    from voicecli.engines.engine import _get_registry  # noqa: PLC0415

    registry = _get_registry()
    nats_cfg = load_nats_config()
    engines = []
    for name in sorted(registry):
        caps = ENGINE_CAPS.get(name, {})
        engines.append(
            {
                "name": name,
                "supports_voice": bool(caps.get("voice")),
                "supports_clone": not bool(caps.get("voice")),
                "vram_gib_est": caps.get("vram_gib"),
            }
        )
    return {
        "engines": engines,
        "samples": list_catalog_entries(),
        "max_cached_engines": nats_cfg.get("max_cached_engines", 1),
        "default_engine": default_engine or _resolve_engine(),
        "catalog_revision": catalog_revision(load_catalog()),
    }


def build_tts_status_data(adapter) -> dict:
    from voicecli.runtime.model_registry import model_registry  # noqa: PLC0415

    return {
        "engines_loaded": model_registry.loaded_engines(),
        "model_loaded": model_registry.loaded_engines(),
        "vram_free_mb": model_registry.vram_free_mb(),
        "vram_status": model_registry.vram_status(),
        "active_requests": adapter.max_concurrent - adapter._sem._value,
        "default_engine": adapter.default_engine,
    }
