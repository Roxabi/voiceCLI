"""voicecli NATS adapters — TTS and STT satellites using roxabi_nats SDK."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voicecli.adapters.nats.transcribe_adapter import SttNatsAdapter
    from voicecli.adapters.nats.synthesize_adapter import TtsNatsAdapter


def __getattr__(name: str):
    if name == "TtsNatsAdapter":
        from voicecli.adapters.nats.synthesize_adapter import TtsNatsAdapter

        return TtsNatsAdapter
    if name == "SttNatsAdapter":
        from voicecli.adapters.nats.transcribe_adapter import SttNatsAdapter

        return SttNatsAdapter
    if name in ("synthesize_adapter", "transcribe_adapter", "config"):
        import importlib

        return importlib.import_module(f"voicecli.adapters.nats.{name}")
    # Backward-compat aliases for old modality-tagged submodule names
    if name == "tts_adapter":
        import importlib

        return importlib.import_module("voicecli.adapters.nats.synthesize_adapter")
    if name == "stt_adapter":
        import importlib

        return importlib.import_module("voicecli.adapters.nats.transcribe_adapter")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["TtsNatsAdapter", "SttNatsAdapter"]
