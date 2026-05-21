# pyright: ignore — excluded from pyrightconfig.json (heavy ML deps, no type stubs)
"""Shared chunked/segmented generation for Chatterbox engine variants.

ChatterboxEngine (multilingual) and ChatterboxTurboEngine share identical
chunked generation and near-identical segmented generation. The only
per-segment difference is the multilingual variant's language_id dispatch;
subclasses override `_segment_kwargs` to return a delta dict merged into
the per-segment kwargs without duplicating the loop body.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from voicecli.engine import TTSEngine
from voicecli.utils import split_sentences

if TYPE_CHECKING:
    from voicecli.markdown import Segment


class ChatterboxBase(TTSEngine):
    """Shared chunked + segmented generation for Chatterbox engine variants."""

    def _load_model(self):  # pragma: no cover — overridden by subclasses
        raise NotImplementedError

    def _segment_kwargs(self, seg: Segment) -> dict:
        """Return a delta dict of variant-specific per-segment kwargs.

        Default: empty (Turbo path). The multilingual subclass overrides this
        to dispatch `language_id` from `seg.language`.
        """
        return {}

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
        from voicecli.utils import concat_audio

        all_wavs: list[np.ndarray] = []
        for i, seg in enumerate(segments):
            print(f"  [{i + 1}/{len(segments)}] {seg.text[:60]}...")
            kw = {**base_kwargs}
            if seg.exaggeration is not None:
                kw["exaggeration"] = seg.exaggeration
            if seg.cfg_weight is not None:
                kw["cfg_weight"] = seg.cfg_weight
            if seg.temperature is not None:
                kw["temperature"] = seg.temperature
            if seg.top_p is not None:
                kw["top_p"] = seg.top_p
            if seg.min_p is not None:
                kw["min_p"] = seg.min_p
            if seg.repetition_penalty is not None:
                kw["repetition_penalty"] = seg.repetition_penalty
            kw.update(self._segment_kwargs(seg))
            audio = self._generate_chunked(seg.text, **kw)
            all_wavs.append(audio)

        gaps = [
            seg.segment_gap if seg.segment_gap is not None else default_gap for seg in segments[1:]
        ]
        xfades = [
            seg.crossfade if seg.crossfade is not None else default_crossfade
            for seg in segments[1:]
        ]
        return concat_audio(all_wavs, self._load_model().sr, gaps, xfades)
