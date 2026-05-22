"""API utilities — TTSResult, engine listing, model warmup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class TTSResult:
    """Result of a TTS generation or cloning operation."""

    wav_path: Path
    mp3_path: Path | None = None
    chunk_paths: list[Path] | None = None


def list_engines() -> list[str]:
    """Return available TTS engine names."""
    from voicecli.engines.engine import available_engines

    return available_engines()


def list_voices(engine: str) -> list[str]:
    """Return available voice names for an engine.

    Raises:
        ValueError: If engine name is unknown.
    """
    from voicecli.engines.engine import get_engine

    try:
        eng = get_engine(engine)
    except ValueError:
        raise ValueError(f"Unknown engine '{engine}'. Available: {list_engines()}")
    return eng.list_voices()


def warmup_model(model: str) -> None:
    """Pre-load a faster-whisper STT model into VRAM.

    Public façade over transcribe._load_model so callers (e.g. the NATS STT
    adapter) do not need to import private symbols directly.

    Args:
        model: Model name accepted by faster-whisper (e.g. "large-v3-turbo").
    """
    from voicecli.runtime.transcribe import _load_model

    _load_model(model)
