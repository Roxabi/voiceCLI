"""API transcribe — speech-to-text transcription."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from voicecli.api.validation import _validate_output_path
from voicecli.core.utils import STT_OUTPUT_DIR, _Unrestricted


def transcribe(
    audio: str | Path,
    *,
    model: str = "large-v3-turbo",
    language: str | None = None,
    output: str | Path | None = None,
    language_detection_threshold: float | None = None,
    language_detection_segments: int | None = None,
    language_fallback: str | None = None,
    initial_prompt: str | None = None,
    task: str = "transcribe",
    segment_context_carry: bool | None = None,
    _skip_daemon: bool = False,
    allowed_base: Path | _Unrestricted = STT_OUTPUT_DIR,
):
    """Transcribe an audio file to text."""
    from voicecli.runtime.transcribe import TranscriptionResult  # noqa: F811
    from voicecli.runtime.transcribe import transcribe as _transcribe

    audio_path = Path(audio)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    result: TranscriptionResult = _transcribe(
        audio_path,
        model=model,
        language=language,
        language_detection_threshold=language_detection_threshold,
        language_detection_segments=language_detection_segments,
        language_fallback=language_fallback,
        initial_prompt=initial_prompt,
        task=task,
        segment_context_carry=segment_context_carry,
        _skip_daemon=_skip_daemon,
    )

    if output is not None:
        out_path = _validate_output_path(Path(output), allowed_base=allowed_base)
        out_path.write_text(result.text, encoding="utf-8")

    return result


async def transcribe_async(*args, **kwargs):
    """Async wrapper for transcribe() — runs in a thread for event loop integration."""
    return await asyncio.to_thread(transcribe, *args, **kwargs)
