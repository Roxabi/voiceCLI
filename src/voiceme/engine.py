from __future__ import annotations

import contextlib
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Generator

_CUDA_PATTERNS = re.compile(
    r"CUDA|cuda|cuDNN|NCCL|out of memory|CUBLAS|CUSOLVER|GPU|"
    r"device-side assert|no kernel image|CUDA_ERROR",
    re.IGNORECASE,
)


@contextlib.contextmanager
def cuda_guard(engine_name: str) -> Generator[None, None, None]:
    """Catch CUDA-related errors and re-raise as friendly SystemExit."""
    try:
        yield
    except (RuntimeError, OSError) as exc:
        msg = str(exc)
        if not _CUDA_PATTERNS.search(msg):
            raise
        print(f"\n{'=' * 60}")
        print(f"  CUDA error while loading {engine_name} engine")
        print(f"{'=' * 60}")
        if "out of memory" in msg.lower():
            print("\n  Your GPU does not have enough VRAM for this model.")
            print("  Try closing other GPU-intensive apps first.")
        elif "no kernel image" in msg.lower() or "not compiled" in msg.lower():
            print("\n  PyTorch was not compiled for your GPU architecture.")
            print("  Reinstall PyTorch matching your CUDA version:")
            print("    https://pytorch.org/get-started/locally/")
        else:
            print(f"\n  {msg[:200]}")
        print("\n  Troubleshooting:")
        print("    1. Check drivers: nvidia-smi")
        print("    2. Check CUDA toolkit: nvcc --version")
        print("    3. Run: voiceme doctor")
        print(f"{'=' * 60}\n")
        raise SystemExit(1) from exc


class TTSEngine(ABC):
    name: str

    @abstractmethod
    def generate(self, text: str, voice: str | None, output_path: Path, **kwargs) -> Path:
        """Generate speech from text using a built-in voice."""

    @abstractmethod
    def clone(
        self, text: str, ref_audio: Path, output_path: Path, ref_text: str | None = None, **kwargs
    ) -> Path:
        """Generate speech by cloning a voice from reference audio."""

    @abstractmethod
    def list_voices(self) -> list[str]:
        """Return available built-in voice names."""


def get_engine(name: str) -> TTSEngine:
    engines = _get_registry()
    if name not in engines:
        raise ValueError(f"Unknown engine '{name}'. Available: {list(engines.keys())}")
    return engines[name]()


def available_engines() -> list[str]:
    return list(_get_registry().keys())


def _get_registry() -> dict[str, type[TTSEngine]]:
    from voiceme.engines.chatterbox import ChatterboxEngine
    from voiceme.engines.chatterbox_turbo import ChatterboxTurboEngine
    from voiceme.engines.qwen import QwenEngine

    return {
        "qwen": QwenEngine,
        "chatterbox": ChatterboxEngine,
        "chatterbox-turbo": ChatterboxTurboEngine,
    }
