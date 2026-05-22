"""VoiceCLI — Unified voice generation CLI and library."""

import sys as _sys

# Import submodule-level types first
from voicecli.api.markdown import Segment, TTSDocument
from voicecli.runtime.transcribe import TranscriptionResult

# Backward-compat shim: external consumers (e.g. lyra) may do:
#   from voicecli.transcribe import TranscriptionResult
# `voicecli.transcribe` no longer exists as a top-level module after the
# runtime/ restructure.  Register the runtime module under the old name so
# those imports keep working without changes on the caller side.
import voicecli.runtime.transcribe as _runtime_transcribe  # noqa: E402

_sys.modules.setdefault("voicecli.transcribe", _runtime_transcribe)

# Import API functions last.
from voicecli.api import (  # noqa: E402
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

__version__ = "0.2.1"

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
