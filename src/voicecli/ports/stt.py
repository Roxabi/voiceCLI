"""Domain port: STTEnginePort Protocol.

This module contains only the abstract interface — no torch, no CUDA,
no infrastructure imports. Adapters and engine implementations import
from here; infrastructure wiring lives in voicecli.transcribe.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from voicecli.runtime.transcribe import TranscriptionResult


@runtime_checkable
class STTEnginePort(Protocol):
    """Protocol for STT engine implementations."""

    def transcribe(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,
        language_detection_threshold: float | None,
        language_detection_segments: int | None,
        language_fallback: str | None,
        task: str,
        initial_prompt: str | None,
        _skip_daemon: bool,
    ) -> TranscriptionResult: ...

    def warmup(self, model: str) -> None: ...
