"""Mock TTS engine for testing only.

Returns 1-second silent WAV audio without any model loading or external deps.
Activated only when VOICECLI_ENABLE_MOCK_ENGINE=1.
"""

from __future__ import annotations

import struct
from pathlib import Path

from voicecli.engine import TTSEngine


def _silent_wav_bytes() -> bytes:
    """Build a 1-second silent WAV (22050 Hz, 16-bit, mono) using struct.pack."""
    sample_rate = 22050
    num_channels = 1
    bits_per_sample = 16
    num_samples = sample_rate  # 1 second
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = num_samples * block_align
    chunk_size = 36 + data_size

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        chunk_size,
        b"WAVE",
        b"fmt ",
        16,  # PCM subchunk size
        1,  # PCM format
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + bytes(data_size)


_SILENT_WAV: bytes = _silent_wav_bytes()


class MockEngine(TTSEngine):
    """Test-only engine — writes silent WAV, no model loading."""

    name = "mock"

    def generate(self, text: str, voice: str | None, output_path: Path, **kwargs) -> Path:
        output_path.write_bytes(_SILENT_WAV)
        return output_path

    def clone(
        self,
        text: str,
        ref_audio: Path,
        output_path: Path,
        ref_text: str | None = None,
        **kwargs,
    ) -> Path:
        output_path.write_bytes(_SILENT_WAV)
        return output_path

    def list_voices(self) -> list[str]:
        return ["mock-voice"]
