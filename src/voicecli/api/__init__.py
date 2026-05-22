"""Public API surface — TTS/STT entry points + markdown parsing."""

from voicecli.api.api import (
    TTSResult,
    clone,
    clone_async,
    generate,
    generate_async,
    list_engines,
    list_voices,
    transcribe,
    transcribe_async,
    warmup_model,
)
from voicecli.api.markdown import Segment, TTSDocument

__all__ = [
    "TTSResult",
    "clone",
    "clone_async",
    "generate",
    "generate_async",
    "list_engines",
    "list_voices",
    "transcribe",
    "transcribe_async",
    "warmup_model",
    "Segment",
    "TTSDocument",
]
