# pyright: ignore — excluded from pyrightconfig.json (heavy ML deps, no type stubs)
from __future__ import annotations

import soundfile as sf
from pathlib import Path
from typing import TYPE_CHECKING

from voicecli.engine import cuda_guard
from voicecli.engines._chatterbox_base import ChatterboxBase
from voicecli.models import CHATTERBOX_MODEL, warn_if_first_download
from voicecli.utils import resolve_language as _resolve_language

if TYPE_CHECKING:
    from voicecli.markdown import Segment


class ChatterboxEngine(ChatterboxBase):
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

    def _apply_segment_overrides(self, kw: dict, seg: Segment) -> None:
        if seg.language is not None:
            kw["language_id"] = _resolve_language(seg.language)

    def generate(self, text: str, voice: str | None, output_path: Path, **kwargs) -> Path:
        language = _resolve_language(kwargs.get("language", "English"))
        exaggeration = kwargs.get("exaggeration", 0.5)
        cfg_weight = kwargs.get("cfg_weight", 0.5)
        temperature = kwargs.get("temperature", 0.8)
        top_p = kwargs.get("top_p", 1.0)
        min_p = kwargs.get("min_p", 0.05)
        repetition_penalty = kwargs.get("repetition_penalty", 2.0)
        segments: list[Segment] | None = kwargs.get("segments")
        default_gap = kwargs.get("segment_gap", 0)
        default_crossfade = kwargs.get("crossfade", 0)

        if segments and len(segments) > 1:
            base_kwargs = dict(
                language_id=language,
                exaggeration=exaggeration,
                cfg_weight=cfg_weight,
                temperature=temperature,
                top_p=top_p,
                min_p=min_p,
                repetition_penalty=repetition_penalty,
            )
            audio = self._generate_segmented(
                segments,
                base_kwargs,
                default_gap=default_gap,
                default_crossfade=default_crossfade,
            )
            sf.write(str(output_path), audio, self._load_model().sr)
            return output_path

        gen_kwargs = dict(
            language_id=language,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
            temperature=temperature,
            top_p=top_p,
            min_p=min_p,
            repetition_penalty=repetition_penalty,
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
        temperature = kwargs.get("temperature", 0.8)
        top_p = kwargs.get("top_p", 1.0)
        min_p = kwargs.get("min_p", 0.05)
        repetition_penalty = kwargs.get("repetition_penalty", 2.0)
        segments: list[Segment] | None = kwargs.get("segments")
        default_gap = kwargs.get("segment_gap", 0)
        default_crossfade = kwargs.get("crossfade", 0)

        if segments and len(segments) > 1:
            base_kwargs = dict(
                audio_prompt_path=str(ref_audio),
                language_id=language,
                exaggeration=exaggeration,
                cfg_weight=cfg_weight,
                temperature=temperature,
                top_p=top_p,
                min_p=min_p,
                repetition_penalty=repetition_penalty,
            )
            audio = self._generate_segmented(
                segments,
                base_kwargs,
                default_gap=default_gap,
                default_crossfade=default_crossfade,
            )
            sf.write(str(output_path), audio, self._load_model().sr)
            return output_path

        gen_kwargs = dict(
            audio_prompt_path=str(ref_audio),
            language_id=language,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
            temperature=temperature,
            top_p=top_p,
            min_p=min_p,
            repetition_penalty=repetition_penalty,
        )
        audio = self._generate_chunked(text, **gen_kwargs)
        sf.write(str(output_path), audio, self._load_model().sr)
        return output_path

    def list_voices(self) -> list[str]:
        return ["default"]
