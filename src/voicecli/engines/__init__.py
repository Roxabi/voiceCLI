# pyright: ignore — excluded from pyrightconfig.json (heavy ML deps, no type stubs)
from voicecli.engines.engine import (  # noqa: E402
    QWEN_ENGINES,
    VRAM_REQUIRED_GB,
    VRAM_REQUIRED_GB_DEFAULT,
    TTSEngine,
    available_engines,
    check_vram,
    cuda_guard,
    get_engine,
    _get_registry,
)

__all__ = [
    "QWEN_ENGINES",
    "VRAM_REQUIRED_GB",
    "VRAM_REQUIRED_GB_DEFAULT",
    "TTSEngine",
    "available_engines",
    "check_vram",
    "cuda_guard",
    "get_engine",
    "_get_registry",
]
