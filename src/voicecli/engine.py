from __future__ import annotations

import contextlib
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

log = logging.getLogger(__name__)

_CUDA_PATTERNS = re.compile(
    r"CUDA|cuDNN|NCCL|out of memory|CUBLAS|CUSOLVER|GPU|"
    r"device-side assert|no kernel image|CUDA_ERROR",
    re.IGNORECASE,
)

# Minimum free VRAM (GB) required to load each engine.
# Used by cuda_guard (standalone) and daemon pre-check.
VRAM_REQUIRED_GB: dict[str, float] = {
    "qwen": 5.0,
    "qwen-fast": 5.0,
    "chatterbox": 2.0,
    "chatterbox-turbo": 2.0,
    "voxtral": 4.0,
}
VRAM_REQUIRED_GB_DEFAULT = 4.0


def check_vram(engine_name: str) -> None:
    """Raise RuntimeError if not enough free VRAM to load the engine."""
    try:
        import torch

        if not torch.cuda.is_available():
            return
        free_bytes, _ = torch.cuda.mem_get_info()
        free_gb = free_bytes / (1024**3)
        required_gb = VRAM_REQUIRED_GB.get(engine_name, VRAM_REQUIRED_GB_DEFAULT)
        if free_gb < required_gb:
            raise RuntimeError(
                f"CUDA error in {engine_name}: not enough VRAM to load model — "
                f"{free_gb:.1f} GB free, need {required_gb:.1f} GB. "
                f"Stop other GPU processes first (e.g. voicecli serve, voicecli dictate)."
            )
    except RuntimeError:
        raise
    except Exception:
        pass  # if check fails, let it try


@contextlib.contextmanager
def cuda_guard(engine_name: str) -> Iterator[None]:
    """Check VRAM availability, then catch CUDA errors and re-raise as RuntimeError."""
    check_vram(engine_name)
    try:
        yield
    except (RuntimeError, OSError) as exc:
        msg = str(exc)
        if not _CUDA_PATTERNS.search(msg):
            raise
        raise RuntimeError(f"CUDA error in {engine_name}: {msg}") from exc


class TTSEngine(ABC):
    name: str
    _small: bool = False

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
    if not engines:
        raise ValueError(
            "No engines available. Install torch for real engines, "
            "or set VOICECLI_ENABLE_MOCK_ENGINE=1 for mock engine."
        )
    if name not in engines:
        raise ValueError(f"Unknown engine '{name}'. Available: {list(engines.keys())}")
    return engines[name]()


def available_engines() -> list[str]:
    return list(_get_registry().keys())


def _get_registry() -> dict[str, type[TTSEngine]]:
    """Build the engine registry.

    MockEngine is gated by ``VOICECLI_ENABLE_MOCK_ENGINE`` — unset in prod,
    set to "1" in the e2e docker-compose files so the NATS satellite can
    answer round-trip requests without loading a real model.

    Real engine imports are wrapped in try/except to handle missing torch.
    If ImportError occurs (torch not installed), real engines are skipped.
    """
    from voicecli.env import coerce_bool_env

    registry: dict[str, type[TTSEngine]] = {}

    # Try to load real engines — skip if torch unavailable
    try:
        from voicecli.engines.chatterbox import ChatterboxEngine
        from voicecli.engines.chatterbox_turbo import ChatterboxTurboEngine
        from voicecli.engines.qwen import QwenEngine
        from voicecli.engines.qwen_fast import QwenFastEngine
        from voicecli.engines.voxtral import VoxtralEngine

        registry.update(
            {
                "qwen": QwenEngine,
                "qwen-fast": QwenFastEngine,
                "chatterbox": ChatterboxEngine,
                "chatterbox-turbo": ChatterboxTurboEngine,
                "voxtral": VoxtralEngine,
            }
        )
    except ImportError as e:
        log.debug("Skipping real engines: %s", e)

    if coerce_bool_env("VOICECLI_ENABLE_MOCK_ENGINE"):
        from voicecli.engines.mock import MockEngine

        registry["mock"] = MockEngine
    return registry
