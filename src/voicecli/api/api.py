"""Public library API for voiceCLI — thin re-export shim.

All implementation lives in focused sub-modules so this file stays ≤50 LOC.
"""

from __future__ import annotations

from voicecli.api.clone import clone, clone_async
from voicecli.api.generate import generate, generate_async
from voicecli.api.transcribe import transcribe, transcribe_async
from voicecli.api.utils import TTSResult, list_engines, list_voices, warmup_model
from voicecli.api.validation import ParamValidationError

__all__ = [
    "clone",
    "clone_async",
    "generate",
    "generate_async",
    "transcribe",
    "transcribe_async",
    "TTSResult",
    "list_engines",
    "list_voices",
    "warmup_model",
    "ParamValidationError",
]
