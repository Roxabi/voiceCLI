"""voicecli.api sub-package — re-exports public surface for backward compatibility."""

from voicecli.api.api import (
    ParamValidationError,
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
from voicecli.api.markdown_types import Segment, TTSDocument

__all__ = [
    # api.api
    "ParamValidationError",
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
    # api.markdown_types
    "Segment",
    "TTSDocument",
]
