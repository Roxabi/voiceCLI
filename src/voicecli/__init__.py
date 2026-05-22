"""VoiceCLI — Unified voice generation CLI and library."""

import sys as _sys

# Import submodule-level types first
from voicecli.api.markdown_types import Segment, TTSDocument
from voicecli.runtime.transcribe import TranscriptionResult

# Backward-compat shim: external consumers (e.g. lyra) may do:
#   from voicecli.transcribe import TranscriptionResult
# `voicecli.transcribe` no longer exists as a top-level module after the
# runtime/ restructure.  Register the runtime module under the old name so
# those imports keep working without changes on the caller side.
# Consumer: lyra/voicecli_client.py (from voicecli.transcribe import TranscriptionResult)
# Removal: safe once lyra updates its import to voicecli.runtime.transcribe
# Tracking: #170 follow-up
import voicecli.runtime.transcribe as _runtime_transcribe  # noqa: E402

_sys.modules.setdefault("voicecli.transcribe", _runtime_transcribe)

# INVARIANT: sys.modules shim above must be registered before this import runs.
# `from voicecli.api import transcribe` overwrites the `voicecli.transcribe` attribute
# with the callable, but sys.modules["voicecli.transcribe"] stays as the module so
# `from voicecli.transcribe import X` keeps working. Do not move imports above the shim.
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
