from __future__ import annotations

import numpy as np
import soundfile as sf
import torch
from pathlib import Path
from typing import TYPE_CHECKING

from voiceme.engine import TTSEngine, cuda_guard
from voiceme.models import (
    QWEN_CLONE_MODEL, QWEN_CLONE_MODEL_SMALL, QWEN_MODEL, QWEN_MODEL_SMALL,
    warn_if_first_download,
)

if TYPE_CHECKING:
    from voiceme.markdown import Segment

SPEAKERS = [
    "Vivian", "Serena", "Uncle_Fu", "Dylan",
    "Eric", "Ryan", "Aiden", "Ono_Anna", "Sohee",
]


class QwenEngine(TTSEngine):
    name = "qwen"

    def __init__(self):
        self._model = None
        self._clone_model = None
        self._small = False  # use 0.6B models

    def _load_model(self):
        if self._model is None:
            with cuda_guard("qwen"):
                from qwen_tts import Qwen3TTSModel

                torch.set_float32_matmul_precision("high")
                repo = QWEN_MODEL_SMALL if self._small else QWEN_MODEL
                warn_if_first_download(repo)
                print(f"[qwen] Loading {repo}...")
                kwargs = {"device_map": "cuda:0", "dtype": torch.bfloat16}
                try:
                    kwargs["attn_implementation"] = "flash_attention_2"
                    self._model = Qwen3TTSModel.from_pretrained(repo, **kwargs)
                except Exception:
                    kwargs.pop("attn_implementation", None)
                    self._model = Qwen3TTSModel.from_pretrained(repo, **kwargs)
                print("[qwen] Model loaded.")
        return self._model

    def _load_clone_model(self):
        if self._clone_model is None:
            with cuda_guard("qwen"):
                from qwen_tts import Qwen3TTSModel

                torch.set_float32_matmul_precision("high")
                repo = QWEN_CLONE_MODEL_SMALL if self._small else QWEN_CLONE_MODEL
                warn_if_first_download(repo)
                print(f"[qwen] Loading {repo}...")
                kwargs = {"device_map": "cuda:0", "dtype": torch.bfloat16}
                try:
                    kwargs["attn_implementation"] = "flash_attention_2"
                    self._clone_model = Qwen3TTSModel.from_pretrained(repo, **kwargs)
                except Exception:
                    kwargs.pop("attn_implementation", None)
                    self._clone_model = Qwen3TTSModel.from_pretrained(repo, **kwargs)
                print("[qwen] Clone model loaded.")
        return self._clone_model

    def _generate_segmented(
        self,
        segments: list[Segment],
        base_kwargs: dict,
        method: str = "custom_voice",
        default_gap: int = 0,
        default_crossfade: int = 0,
    ) -> tuple[np.ndarray, int]:
        """Generate audio per-segment with individual overrides, then concatenate."""
        from voiceme.utils import concat_audio

        if method == "custom_voice":
            model = self._load_model()
            gen_fn = model.generate_custom_voice
        else:
            model = self._load_clone_model()
            gen_fn = model.generate_voice_clone
            # Extract voice clone prompt ONCE, reuse for all segments
            if "ref_audio" in base_kwargs:
                prompt = model.create_voice_clone_prompt(
                    ref_audio=base_kwargs["ref_audio"],
                    ref_text=base_kwargs.get("ref_text"),
                    x_vector_only_mode=base_kwargs.get("x_vector_only_mode", False),
                )
                # Replace ref_audio/ref_text with pre-computed prompt
                base_kwargs = {
                    k: v for k, v in base_kwargs.items()
                    if k not in ("ref_audio", "ref_text", "x_vector_only_mode")
                }
                base_kwargs["voice_clone_prompt"] = prompt

        all_wavs: list[np.ndarray] = []
        sr = None

        for i, seg in enumerate(segments):
            instruct_label = seg.instruct or "(no instruct)"
            print(f"  [{i + 1}/{len(segments)}] instruct={instruct_label}")
            print(f"    {seg.text[:80]}{'...' if len(seg.text) > 80 else ''}")

            kw = {**base_kwargs, "text": seg.text}
            if seg.instruct:
                kw["instruct"] = seg.instruct
            else:
                kw.pop("instruct", None)
            if seg.language:
                kw["language"] = seg.language
            if seg.voice:
                kw["speaker"] = seg.voice

            wavs, sr = gen_fn(**kw)
            all_wavs.append(wavs[0])

        gaps = [
            seg.segment_gap if seg.segment_gap is not None else default_gap
            for seg in segments[1:]
        ]
        xfades = [
            seg.crossfade if seg.crossfade is not None else default_crossfade
            for seg in segments[1:]
        ]
        return concat_audio(all_wavs, sr, gaps, xfades), sr

    def generate(self, text: str, voice: str | None, output_path: Path, **kwargs) -> Path:
        model = self._load_model()
        voice = voice or "Ono_Anna"
        if voice not in SPEAKERS:
            raise ValueError(f"Unknown voice '{voice}'. Available: {SPEAKERS}")

        language = kwargs.get("language", "English")
        instruct = kwargs.get("instruct")
        segments: list[Segment] | None = kwargs.get("segments")
        default_gap = kwargs.get("segment_gap", 0)
        default_crossfade = kwargs.get("crossfade", 0)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Multi-segment mode
        if segments and len(segments) > 1:
            base_kwargs = dict(language=language, speaker=voice)
            audio, sr = self._generate_segmented(
                segments, base_kwargs, method="custom_voice",
                default_gap=default_gap, default_crossfade=default_crossfade,
            )
            sf.write(str(output_path), audio, sr)
            return output_path

        # Single-shot mode (backward compatible)
        gen_kwargs = dict(text=text, language=language, speaker=voice)
        if instruct:
            gen_kwargs["instruct"] = instruct

        wavs, sr = model.generate_custom_voice(**gen_kwargs)
        sf.write(str(output_path), wavs[0], sr)
        return output_path

    def clone(
        self, text: str, ref_audio: Path, output_path: Path, ref_text: str | None = None, **kwargs
    ) -> Path:
        language = kwargs.get("language", "English")
        segments: list[Segment] | None = kwargs.get("segments")
        default_gap = kwargs.get("segment_gap", 0)
        default_crossfade = kwargs.get("crossfade", 0)

        base_kwargs: dict = dict(language=language, ref_audio=str(ref_audio))
        if ref_text:
            base_kwargs["ref_text"] = ref_text
        else:
            base_kwargs["x_vector_only_mode"] = True

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Multi-segment mode
        if segments and len(segments) > 1:
            audio, sr = self._generate_segmented(
                segments, base_kwargs, method="clone",
                default_gap=default_gap, default_crossfade=default_crossfade,
            )
            sf.write(str(output_path), audio, sr)
            return output_path

        # Single-shot mode (backward compatible)
        model = self._load_clone_model()
        gen_kwargs = {**base_kwargs, "text": text}
        wavs, sr = model.generate_voice_clone(**gen_kwargs)
        sf.write(str(output_path), wavs[0], sr)
        return output_path

    def list_voices(self) -> list[str]:
        return list(SPEAKERS)
