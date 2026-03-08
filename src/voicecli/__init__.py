"""VoiceCLI — Unified voice generation CLI and library."""

__version__ = "0.1.0"

# Import submodule-level types first (this registers voicecli.transcribe as a submodule)
from voicecli.markdown import Segment, TTSDocument
from voicecli.transcribe import TranscriptionResult

# Import API functions last — the `transcribe` function overwrites the submodule attribute
from voicecli.api import (
    TTSResult,
    clone,
    clone_async,
    generate,
    generate_async,
    list_engines,
    list_voices,
    transcribe,
    transcribe_async,
)

__all__ = [
    "TTSResult",
    "TranscriptionResult",
    "TTSDocument",
    "Segment",
    "generate",
    "generate_async",
    "clone",
    "clone_async",
    "transcribe",
    "transcribe_async",
    "list_engines",
    "list_voices",
    "__version__",
]
