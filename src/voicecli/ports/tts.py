"""Domain port: TTSEngine ABC.

This module contains only the abstract interface — no torch, no CUDA,
no infrastructure imports. Adapters and engine implementations import
from here; infrastructure wiring lives in voicecli.engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


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
