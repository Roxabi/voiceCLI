# pyright: ignore — excluded from pyrightconfig.json (heavy ML deps, no type stubs)
from voicecli.engines.engine import (  # noqa: F401
    QWEN_ENGINES,
    VRAM_REQUIRED_GB,
    VRAM_REQUIRED_GB_DEFAULT,
    _get_registry,
    available_engines,
    check_vram,
    cuda_guard,
    get_engine,
)

__all__ = ["get_engine", "available_engines", "QWEN_ENGINES"]
