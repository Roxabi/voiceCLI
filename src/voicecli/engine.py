from __future__ import annotations

import contextlib
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

_CUDA_PATTERNS = re.compile(
    r"CUDA|cuDNN|NCCL|out of memory|CUBLAS|CUSOLVER|GPU|"
    r"device-side assert|no kernel image|CUDA_ERROR",
    re.IGNORECASE,
)


@contextlib.contextmanager
def cuda_guard(engine_name: str) -> Iterator[None]:
    """Catch CUDA-related errors and re-raise as RuntimeError."""
    try:
        yield
    except (RuntimeError, OSError) as exc:
        msg = str(exc)
        if not _CUDA_PATTERNS.search(msg):
            raise
        raise RuntimeError(f"CUDA error in {engine_name}: {msg}") from exc


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


QWEN_ENGINES = frozenset({"qwen", "qwen-fast"})


def get_engine(name: str) -> TTSEngine:
    engines = _get_registry()
    if name not in engines:
        raise ValueError(f"Unknown engine '{name}'. Available: {list(engines.keys())}")
    return engines[name]()


def available_engines() -> list[str]:
    return list(_get_registry().keys())


def _get_registry() -> dict[str, type[TTSEngine]]:
    from voicecli.engines.chatterbox import ChatterboxEngine
    from voicecli.engines.chatterbox_turbo import ChatterboxTurboEngine
    from voicecli.engines.qwen import QwenEngine
    from voicecli.engines.qwen_fast import QwenFastEngine

    return {
        "qwen": QwenEngine,
        "qwen-fast": QwenFastEngine,
        "chatterbox": ChatterboxEngine,
        "chatterbox-turbo": ChatterboxTurboEngine,
    }
