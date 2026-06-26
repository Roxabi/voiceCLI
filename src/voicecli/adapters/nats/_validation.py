"""Re-export voice ingress validation from ``roxabi_satellite``."""

from roxabi_satellite.voice.validation import (
    SttValidationOutcome,
    TtsValidationOutcome,
    validate_stt_request,
    validate_tts_request,
)

__all__ = [
    "SttValidationOutcome",
    "TtsValidationOutcome",
    "validate_stt_request",
    "validate_tts_request",
]
