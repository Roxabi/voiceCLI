"""voicecli NATS adapters — TTS and STT satellites using roxabi_nats SDK."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voicecli.adapters.nats.stt_adapter import SttNatsAdapter
    from voicecli.adapters.nats.tts_adapter import TtsNatsAdapter


def __getattr__(name: str):
    if name == "TtsNatsAdapter":
        from voicecli.adapters.nats.tts_adapter import TtsNatsAdapter

        return TtsNatsAdapter
    if name == "SttNatsAdapter":
        from voicecli.adapters.nats.stt_adapter import SttNatsAdapter

        return SttNatsAdapter
    if name in ("tts_adapter", "stt_adapter", "config"):
        import importlib

        return importlib.import_module(f"voicecli.adapters.nats.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["TtsNatsAdapter", "SttNatsAdapter"]
