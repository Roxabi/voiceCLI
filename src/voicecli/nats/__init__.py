"""voicecli NATS adapters — TTS and STT satellites using roxabi_nats SDK."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voicecli.nats.stt_adapter import SttNatsAdapter
    from voicecli.nats.tts_adapter import TtsNatsAdapter


def __getattr__(name: str):
    if name == "TtsNatsAdapter":
        from voicecli.nats.tts_adapter import TtsNatsAdapter

        return TtsNatsAdapter
    if name == "SttNatsAdapter":
        from voicecli.nats.stt_adapter import SttNatsAdapter

        return SttNatsAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["TtsNatsAdapter", "SttNatsAdapter"]
