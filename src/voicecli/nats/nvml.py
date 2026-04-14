"""Lazy pynvml probe for VRAM usage."""

from __future__ import annotations

from typing import Any


def _import_pynvml() -> Any:
    try:
        import pynvml  # type: ignore[import-not-found]

        return pynvml
    except ImportError:
        return None


def read_vram() -> tuple[int | None, int | None]:
    """Return (used_mb, total_mb) from device 0, or (None, None) on any failure."""
    pynvml = _import_pynvml()
    if pynvml is None:
        return (None, None)
    try:
        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            return (info.used // (1024**2), info.total // (1024**2))
        finally:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
    except Exception:
        return (None, None)
