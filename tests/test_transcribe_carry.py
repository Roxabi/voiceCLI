"""Tests for cross-VAD-chunk context carry in runtime.transcribe."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from voicecli.runtime.transcribe import (
    Segment,
    _build_carry_prompt,
    _resolve_segment_context_carry,
    _transcribe_single_pass,
    _transcribe_with_segment_carry,
)


class TestBuildCarryPrompt:
    def test_returns_base_when_no_accumulated(self):
        assert _build_carry_prompt("Bonjour.", "") == "Bonjour."
        assert _build_carry_prompt("Bonjour.", "   ") == "Bonjour."

    def test_merges_base_and_accumulated(self):
        result = _build_carry_prompt("Bonjour.", "Je continue ici.")
        assert result == "Bonjour. Je continue ici."

    def test_returns_accumulated_when_no_base(self):
        assert _build_carry_prompt(None, "Suite sans base.") == "Suite sans base."

    def test_truncates_long_accumulated_tail(self):
        long_text = "a" * 500
        result = _build_carry_prompt("Base.", long_text)
        assert result is not None
        assert result.startswith("Base.")
        assert len(result) < len(long_text) + 10


class TestTranscribeWithSegmentCarry:
    def test_second_vad_chunk_receives_carry_prompt(self, tmp_path: Path):
        audio_path = tmp_path / "dictate.wav"
        audio_path.write_bytes(b"fake")

        speech_chunks = [
            {"start": 0, "end": 8000},
            {"start": 88000, "end": 160000},
        ]
        fake_audio = np.zeros(160000, dtype=np.float32)

        class FakeSegment:
            def __init__(self, start: float, end: float, text: str):
                self.start = start
                self.end = end
                self.text = text

        prompts: list[str | None] = []

        def fake_transcribe(audio, **kwargs):
            prompt = kwargs.get("initial_prompt")
            prompts.append(prompt)
            if len(prompts) == 1:
                return [FakeSegment(0.0, 0.5, "Première phrase.")], MagicMock(language="fr")
            return [FakeSegment(0.0, 0.5, "deuxième phrase.")], MagicMock(language="fr")

        whisper = MagicMock()
        whisper.transcribe.side_effect = fake_transcribe

        with (
            patch("faster_whisper.audio.decode_audio", return_value=fake_audio),
            patch(
                "faster_whisper.vad.get_speech_timestamps",
                return_value=speech_chunks,
            ),
        ):
            seg_list, _info = _transcribe_with_segment_carry(
                whisper,
                audio_path,
                language="fr",
                task="transcribe",
                initial_prompt="Bonjour.",
            )

        assert prompts == ["Bonjour.", "Bonjour. Première phrase."]
        assert len(seg_list) == 2
        assert seg_list[0].text == "Première phrase."
        assert seg_list[1].text == "deuxième phrase."
        assert seg_list[1].start == pytest.approx(88000 / 16000.0)


class TestResolveSegmentContextCarry:
    def test_explicit_true(self):
        assert _resolve_segment_context_carry(True) is True

    def test_explicit_false(self):
        assert _resolve_segment_context_carry(False) is False

    def test_defaults_true_when_unset(self, tmp_path):
        with patch("voicecli.core.config._find_config", return_value=None):
            assert _resolve_segment_context_carry(None) is True

    def test_reads_false_from_config(self, tmp_path):
        toml_file = tmp_path / "voicecli.toml"
        toml_file.write_text("[stt]\nsegment_context_carry = false\n")
        with patch("voicecli.core.config._find_config", return_value=toml_file):
            assert _resolve_segment_context_carry(None) is False


class TestTranscribeSinglePass:
    def test_uses_vad_filter_and_no_condition_on_previous_text(self, tmp_path: Path):
        audio_path = tmp_path / "dictate.wav"
        audio_path.write_bytes(b"fake")

        class FakeSegment:
            def __init__(self, start: float, end: float, text: str):
                self.start = start
                self.end = end
                self.text = text

        captured: dict = {}

        def fake_transcribe(path, **kwargs):
            captured.update(kwargs)
            return [FakeSegment(0.0, 1.0, "Bonjour.")], MagicMock(language="fr")

        whisper = MagicMock()
        whisper.transcribe.side_effect = fake_transcribe

        seg_list, _info = _transcribe_single_pass(
            whisper,
            audio_path,
            language="fr",
            task="transcribe",
            initial_prompt="Base.",
        )

        assert captured["vad_filter"] is True
        assert captured["condition_on_previous_text"] is False
        assert seg_list[0].text == "Bonjour."
