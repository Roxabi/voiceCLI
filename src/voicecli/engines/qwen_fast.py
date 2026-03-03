"""Qwen3-TTS engine with CUDA graph acceleration via faster-qwen3-tts."""

from __future__ import annotations

import numpy as np
import soundfile as sf
import torch
from pathlib import Path
from typing import TYPE_CHECKING

from voicecli.engine import cuda_guard
from voicecli.engines.qwen import QwenEngine
from voicecli.models import (
    QWEN_CLONE_MODEL,
    QWEN_CLONE_MODEL_SMALL,
    QWEN_MODEL,
    QWEN_MODEL_SMALL,
    warn_if_first_download,
)

if TYPE_CHECKING:
    from voicecli.markdown import Segment


class QwenFastEngine(QwenEngine):
    """Qwen3-TTS with CUDA graph capture (5-9x speedup after warmup)."""

    name = "qwen-fast"

    def _load_model(self):
        if self._model is None:
            with cuda_guard("qwen-fast"):
                from faster_qwen3_tts import FasterQwen3TTS

                torch.set_float32_matmul_precision("high")
                repo = QWEN_MODEL_SMALL if self._small else QWEN_MODEL
                warn_if_first_download(repo)
                print(f"[qwen-fast] Loading {repo}...")
                self._model = FasterQwen3TTS.from_pretrained(
                    repo,
                    device="cuda",
                    dtype=torch.bfloat16,
                )
                print("[qwen-fast] Model loaded (CUDA graphs will warm up on first call).")
        return self._model

    def _load_clone_model(self):
        if self._clone_model is None:
            with cuda_guard("qwen-fast"):
                from faster_qwen3_tts import FasterQwen3TTS

                torch.set_float32_matmul_precision("high")
                repo = QWEN_CLONE_MODEL_SMALL if self._small else QWEN_CLONE_MODEL
                warn_if_first_download(repo)
                print(f"[qwen-fast] Loading {repo}...")
                self._clone_model = FasterQwen3TTS.from_pretrained(
                    repo,
                    device="cuda",
                    dtype=torch.bfloat16,
                )
                print("[qwen-fast] Clone model loaded (CUDA graphs will warm up on first call).")
        return self._clone_model

    def _generate_segmented(
        self,
        segments: list[Segment],
        base_kwargs: dict,
        method: str = "custom_voice",
        default_gap: int = 0,
        default_crossfade: int = 0,
    ) -> tuple[np.ndarray, int]:
        """Generate audio per-segment with CUDA-graph-accelerated model."""
        from voicecli.utils import concat_audio

        if method == "custom_voice":
            model = self._load_model()
            gen_fn = model.generate_custom_voice
        else:
            model = self._load_clone_model()
            gen_fn = model.generate_voice_clone
            # Rename x_vector_only_mode → xvec_only for faster-qwen3-tts API
            if "x_vector_only_mode" in base_kwargs:
                base_kwargs["xvec_only"] = base_kwargs.pop("x_vector_only_mode")
            # Keep ref_audio/ref_text in kwargs — library caches voice prompt internally

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
            seg.segment_gap if seg.segment_gap is not None else default_gap for seg in segments[1:]
        ]
        xfades = [
            seg.crossfade if seg.crossfade is not None else default_crossfade
            for seg in segments[1:]
        ]
        return concat_audio(all_wavs, sr, gaps, xfades), sr

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
            base_kwargs["xvec_only"] = True  # faster-qwen3-tts API name

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Multi-segment mode
        if segments and len(segments) > 1:
            audio, sr = self._generate_segmented(
                segments,
                base_kwargs,
                method="clone",
                default_gap=default_gap,
                default_crossfade=default_crossfade,
            )
            sf.write(str(output_path), audio, sr)
            return output_path

        # Single-shot mode
        model = self._load_clone_model()
        gen_kwargs = {**base_kwargs, "text": text}
        wavs, sr = model.generate_voice_clone(**gen_kwargs)
        sf.write(str(output_path), wavs[0], sr)
        return output_path
