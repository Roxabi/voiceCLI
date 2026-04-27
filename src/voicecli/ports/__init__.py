"""voicecli domain ports — abstract interfaces for engine adapters."""

from voicecli.ports.stt import STTEnginePort
from voicecli.ports.synthesis import SynthesisPort
from voicecli.ports.tts import TTSEngine

__all__ = ["STTEnginePort", "SynthesisPort", "TTSEngine"]
