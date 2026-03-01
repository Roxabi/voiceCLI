from __future__ import annotations

import numpy as np
import soundfile as sf
from pathlib import Path
from typing import TYPE_CHECKING

from voiceme.engine import TTSEngine, cuda_guard
from voiceme.models import CHATTERBOX_MODEL, warn_if_first_download
from voiceme.utils import resolve_language as _resolve_language, split_sentences

if TYPE_CHECKING:
    from voiceme.markdown import Segment


class ChatterboxEngine(TTSEngine):
    name = "chatterbox"

    def __init__(self):
        self._model = None

    def _load_model(self):
        if self._model is None:
            with cuda_guard("chatterbox"):
                from chatterbox.mtl_tts import ChatterboxMultilingualTTS

                warn_if_first_download(CHATTERBOX_MODEL)
                print("[chatterbox] Loading multilingual model...")
                self._model = ChatterboxMultilingualTTS.from_pretrained(device="cuda")
                # Multilingual AlignmentStreamAnalyzer needs output_attentions,
                # which requires eager attention (not sdpa)
                if hasattr(self._model, "t3") and hasattr(self._model.t3, "tfmr"):
                    cfg = self._model.t3.tfmr.config
                    if hasattr(cfg, "_attn_implementation"):
                        cfg._attn_implementation = "eager"
                print("[chatterbox] Model loaded.")
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
            if seg.language is not None:
                kw["language_id"] = _resolve_language(seg.language)
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
        language = _resolve_language(kwargs.get("language", "English"))
        exaggeration = kwargs.get("exaggeration", 0.5)
        cfg_weight = kwargs.get("cfg_weight", 0.5)
        segments: list[Segment] | None = kwargs.get("segments")
        default_gap = kwargs.get("segment_gap", 0)
        default_crossfade = kwargs.get("crossfade", 0)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        if segments and len(segments) > 1:
            base_kwargs = dict(
                language_id=language, exaggeration=exaggeration, cfg_weight=cfg_weight,
            )
            audio = self._generate_segmented(
                segments, base_kwargs,
                default_gap=default_gap, default_crossfade=default_crossfade,
            )
            sf.write(str(output_path), audio, self._load_model().sr)
            return output_path

        gen_kwargs = dict(
            language_id=language, exaggeration=exaggeration, cfg_weight=cfg_weight,
        )
        audio = self._generate_chunked(text, **gen_kwargs)
        sf.write(str(output_path), audio, self._load_model().sr)
        return output_path

    def clone(
        self, text: str, ref_audio: Path, output_path: Path, ref_text: str | None = None, **kwargs
    ) -> Path:
        language = _resolve_language(kwargs.get("language", "English"))
        exaggeration = kwargs.get("exaggeration", 0.5)
        # Default cfg_weight=0.0 for cross-language cloning to reduce accent bleed
        cfg_weight = kwargs.get("cfg_weight", 0.0)
        segments: list[Segment] | None = kwargs.get("segments")
        default_gap = kwargs.get("segment_gap", 0)
        default_crossfade = kwargs.get("crossfade", 0)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        if segments and len(segments) > 1:
            base_kwargs = dict(
                audio_prompt_path=str(ref_audio),
                language_id=language, exaggeration=exaggeration, cfg_weight=cfg_weight,
            )
            audio = self._generate_segmented(
                segments, base_kwargs,
                default_gap=default_gap, default_crossfade=default_crossfade,
            )
            sf.write(str(output_path), audio, self._load_model().sr)
            return output_path

        gen_kwargs = dict(
            audio_prompt_path=str(ref_audio),
            language_id=language, exaggeration=exaggeration, cfg_weight=cfg_weight,
        )
        audio = self._generate_chunked(text, **gen_kwargs)
        sf.write(str(output_path), audio, self._load_model().sr)
        return output_path

    def list_voices(self) -> list[str]:
        return ["default"]
