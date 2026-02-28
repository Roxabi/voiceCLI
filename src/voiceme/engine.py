from abc import ABC, abstractmethod
from pathlib import Path


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
