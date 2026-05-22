"""VoiceCLI — Unified voice generation CLI and library."""

__version__ = "0.2.1"

# Submodule attribute trick — keep voicecli.transcribe resolving for legacy callers
import sys as _sys
import voicecli.runtime.transcribe as _runtime_transcribe

_sys.modules["voicecli.transcribe"] = _runtime_transcribe

# Import submodule-level types first
from voicecli.api.markdown import Segment, TTSDocument  # noqa: E402
from voicecli.runtime.transcribe import TranscriptionResult  # noqa: E402

# Import API functions last — the `transcribe` function overwrites the submodule attribute
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

# Re-pin the submodule attribute after the API import overwrites it.
# `import voicecli.transcribe` resolves via sys.modules (already registered above);
# setting the attribute here ensures `voicecli.transcribe.TranscriptionResult`
# works even when accessed as an attribute on the package object.
_sys.modules[__name__].transcribe = _runtime_transcribe  # type: ignore[assignment]

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
