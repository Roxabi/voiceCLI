"""Domain port: SynthesisPort Protocol.

This module contains only the abstract interface for TTS synthesis dispatch —
no daemon, no socket, no infrastructure imports. The use-case layer (api.py)
should depend on this port rather than directly on daemon.py and model_registry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class SynthesisPort(Protocol):
    """Protocol for TTS synthesis backends (daemon socket or local engine)."""

    def generate(
        self,
        engine: str,
        text: str,
        voice: str | None,
        output_path: Path,
        *,
        language: str | None = None,
        instruct: str | None = None,
        exaggeration: float | None = None,
        cfg_weight: float | None = None,
        segment_gap: int | None = None,
        crossfade: int | None = None,
        segments: list | None = None,
        **kwargs,
    ) -> Path | None:
        """Generate speech from text. Returns output path on success, None on failure."""
        ...

    def clone(
        self,
        engine: str,
        text: str,
        ref_audio: Path,
        output_path: Path,
        *,
        ref_text: str | None = None,
        language: str | None = None,
        exaggeration: float | None = None,
        cfg_weight: float | None = None,
        segment_gap: int | None = None,
        crossfade: int | None = None,
        segments: list | None = None,
        **kwargs,
    ) -> Path | None:
        """Clone voice from reference audio. Returns output path on success, None on failure."""
        ...

    def is_available(self) -> bool:
        """Return True if this synthesis backend is ready to accept requests."""
        ...
