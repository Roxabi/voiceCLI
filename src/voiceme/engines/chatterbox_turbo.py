from __future__ import annotations

import numpy as np
import soundfile as sf
from pathlib import Path
from typing import TYPE_CHECKING

from voiceme.engine import TTSEngine, cuda_guard
from voiceme.models import CHATTERBOX_MODEL, warn_if_first_download
from voiceme.utils import split_sentences

if TYPE_CHECKING:
    from voiceme.markdown import Segment


class ChatterboxTurboEngine(TTSEngine):
    name = "chatterbox-turbo"

    def __init__(self):
        self._model = None

    def _load_model(self):
        if self._model is None:
            with cuda_guard("chatterbox-turbo"):
                from chatterbox.tts import ChatterboxTTS

                warn_if_first_download(CHATTERBOX_MODEL)
                print("[chatterbox-turbo] Loading model...")
                self._model = ChatterboxTTS.from_pretrained(device="cuda")
                print("[chatterbox-turbo] Model loaded.")
        return self._model

    def _generate_chunked(self, text: str, **gen_kwargs) -> np.ndarray:
        """Generate audio in sentence-sized chunks and concatenate."""
        model = self._load_model()
        chunks = split_sentences(text)
        wavs = []
        for i, chunk in enumerate(chunks):
            print(f"  [{i + 1}/{len(chunks)}] {chunk[:60]}...")
            kw = {**gen_kwargs, "text": chunk}
            wav = model.generate(**kw)
            wavs.append(wav.squeeze().cpu().numpy())
        return np.concatenate(wavs)

    def _generate_segmented(
        self,
        segments: list[Segment],
        base_kwargs: dict,
        default_gap: int = 0,
        default_crossfade: int = 0,
    ) -> np.ndarray:
        """Generate audio per-segment with individual overrides, then concatenate."""
        from voiceme.utils import concat_audio

        all_wavs: list[np.ndarray] = []
        for i, seg in enumerate(segments):
            print(f"  [{i + 1}/{len(segments)}] {seg.text[:60]}...")
            kw = {**base_kwargs}
            if seg.exaggeration is not None:
                kw["exaggeration"] = seg.exaggeration
            if seg.cfg_weight is not None:
                kw["cfg_weight"] = seg.cfg_weight
            audio = self._generate_chunked(seg.text, **kw)
            all_wavs.append(audio)

        gaps = [
            seg.segment_gap if seg.segment_gap is not None else default_gap
            for seg in segments[1:]
        ]
        xfades = [
            seg.crossfade if seg.crossfade is not None else default_crossfade
            for seg in segments[1:]
        ]
        return concat_audio(all_wavs, self._load_model().sr, gaps, xfades)

    def generate(self, text: str, voice: str | None, output_path: Path, **kwargs) -> Path:
        exaggeration = kwargs.get("exaggeration", 0.5)
        cfg_weight = kwargs.get("cfg_weight", 0.5)
        segments: list[Segment] | None = kwargs.get("segments")
        default_gap = kwargs.get("segment_gap", 0)
        default_crossfade = kwargs.get("crossfade", 0)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        if segments and len(segments) > 1:
            base_kwargs = dict(exaggeration=exaggeration, cfg_weight=cfg_weight)
            audio = self._generate_segmented(
                segments, base_kwargs,
                default_gap=default_gap, default_crossfade=default_crossfade,
            )
            sf.write(str(output_path), audio, self._load_model().sr)
            return output_path

        gen_kwargs = dict(exaggeration=exaggeration, cfg_weight=cfg_weight)
        audio = self._generate_chunked(text, **gen_kwargs)
        sf.write(str(output_path), audio, self._load_model().sr)
        return output_path

    def clone(
        self, text: str, ref_audio: Path, output_path: Path, ref_text: str | None = None, **kwargs
    ) -> Path:
        exaggeration = kwargs.get("exaggeration", 0.5)
        cfg_weight = kwargs.get("cfg_weight", 0.5)
        segments: list[Segment] | None = kwargs.get("segments")
        default_gap = kwargs.get("segment_gap", 0)
        default_crossfade = kwargs.get("crossfade", 0)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        if segments and len(segments) > 1:
            base_kwargs = dict(
                audio_prompt_path=str(ref_audio),
                exaggeration=exaggeration, cfg_weight=cfg_weight,
            )
            audio = self._generate_segmented(
                segments, base_kwargs,
                default_gap=default_gap, default_crossfade=default_crossfade,
            )
            sf.write(str(output_path), audio, self._load_model().sr)
            return output_path

        gen_kwargs = dict(
            audio_prompt_path=str(ref_audio),
            exaggeration=exaggeration, cfg_weight=cfg_weight,
        )
        audio = self._generate_chunked(text, **gen_kwargs)
        sf.write(str(output_path), audio, self._load_model().sr)
        return output_path

    def list_voices(self) -> list[str]:
        return ["default"]
