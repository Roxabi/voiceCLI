# pyright: ignore — excluded from pyrightconfig.json (heavy ML deps, no type stubs)
from __future__ import annotations

import numpy as np
import soundfile as sf
from pathlib import Path
from typing import TYPE_CHECKING

from voicecli.engines.engine import TTSEngine, cuda_guard
from voicecli.models import VOXTRAL_MODEL, warn_if_first_download

if TYPE_CHECKING:
    from voicecli.api.markdown import Segment

# Voxtral outputs 48 kHz after post-processing
_SAMPLE_RATE = 48000

# Language → voice prefix mapping for automatic voice selection
_LANG_VOICE_PREFIX: dict[str, str] = {
    "fr": "fr",
    "de": "de",
    "es": "es",
    "it": "it",
    "pt": "pt",
    "nl": "nl",
    "ar": "ar",
    "hi": "hi",
}


def _resolve_voice(voice: str | None, language: str | None) -> str:
    """Resolve voice name, falling back to a language-appropriate default."""
    if voice:
        return voice
    if language:
        from voicecli.utils import resolve_language

        lang_code = resolve_language(language)
        prefix = _LANG_VOICE_PREFIX.get(lang_code)
        if prefix:
            return f"{prefix}_female"
    return "neutral_female"


class VoxtralEngine(TTSEngine):
    name = "voxtral"

    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._voice_dir: str | None = None

    def _model_dir(self) -> Path:
        """Resolve HuggingFace cache path for Voxtral weights."""
        from voicecli.models import _model_cache_path

        cache = _model_cache_path(VOXTRAL_MODEL)
        snapshots = cache / "snapshots"
        if snapshots.exists():
            # Use latest snapshot
            for snap in sorted(snapshots.iterdir(), reverse=True):
                if snap.is_dir():
                    return snap
        return cache

    def _load_model(self):
        if self._model is not None:
            return self._model

        with cuda_guard("voxtral"):
            from voxtral_tts import load_model_int4, TekkenTokenizer, enable_static_cache

            warn_if_first_download(VOXTRAL_MODEL)
            model_dir = self._model_dir()
            print("[voxtral] Loading model (int4 HQQ)...")
            self._model = load_model_int4(str(model_dir))
            self._tokenizer = TekkenTokenizer(str(model_dir / "tekken.json"))
            self._voice_dir = str(model_dir / "voice_embedding")
            enable_static_cache(self._model, max_seq_len=800)
            print("[voxtral] Model loaded.")
        return self._model

    def _generate_one(
        self,
        text: str,
        voice: str,
        *,
        flow_steps: int = 8,
        cfg_alpha: float = 1.2,
    ) -> np.ndarray:
        """Generate audio for a single text chunk."""
        import torch
        from voxtral_tts import generate_speech_fast

        model = self._load_model()
        with torch.inference_mode():
            audio, _ = generate_speech_fast(
                model,
                self._tokenizer,
                text,
                voice_name=voice,
                voice_dir=self._voice_dir,
                max_frames=500,
                device="cuda",
                flow_steps=flow_steps,
                cfg_alpha=cfg_alpha,
            )
        return audio

    def _generate_segmented(
        self,
        segments: list[Segment],
        voice: str,
        default_gap: int = 0,
        default_crossfade: int = 0,
        *,
        flow_steps: int = 8,
        cfg_alpha: float = 1.2,
    ) -> np.ndarray:
        """Generate audio per-segment with per-segment voice/language overrides."""
        from voicecli.utils import concat_audio

        all_wavs: list[np.ndarray] = []
        for i, seg in enumerate(segments):
            print(f"  [{i + 1}/{len(segments)}] {seg.text[:60]}...")
            seg_voice = (
                _resolve_voice(seg.voice, seg.language) if seg.voice or seg.language else voice
            )
            seg_flow = seg.flow_steps if seg.flow_steps is not None else flow_steps
            seg_cfg = seg.cfg_alpha if seg.cfg_alpha is not None else cfg_alpha
            audio = self._generate_one(seg.text, seg_voice, flow_steps=seg_flow, cfg_alpha=seg_cfg)
            all_wavs.append(audio)

        gaps = [
            seg.segment_gap if seg.segment_gap is not None else default_gap for seg in segments[1:]
        ]
        xfades = [
            seg.crossfade if seg.crossfade is not None else default_crossfade
            for seg in segments[1:]
        ]
        return concat_audio(all_wavs, _SAMPLE_RATE, gaps, xfades)

    def generate(self, text: str, voice: str | None, output_path: Path, **kwargs) -> Path:
        language = kwargs.get("language")
        segments: list[Segment] | None = kwargs.get("segments")
        default_gap = kwargs.get("segment_gap", 0)
        default_crossfade = kwargs.get("crossfade", 0)
        flow_steps = kwargs.get("flow_steps", 8)
        cfg_alpha = kwargs.get("cfg_alpha", 1.2)

        resolved_voice = _resolve_voice(voice, language)

        if segments and len(segments) > 1:
            audio = self._generate_segmented(
                segments,
                resolved_voice,
                default_gap=default_gap,
                default_crossfade=default_crossfade,
                flow_steps=flow_steps,
                cfg_alpha=cfg_alpha,
            )
        else:
            audio = self._generate_one(
                text, resolved_voice, flow_steps=flow_steps, cfg_alpha=cfg_alpha
            )

        sf.write(str(output_path), audio, _SAMPLE_RATE)
        return output_path

    def clone(
        self, text: str, ref_audio: Path, output_path: Path, ref_text: str | None = None, **kwargs
    ) -> Path:
        raise NotImplementedError(
            "Voxtral voice cloning is not available — "
            "Mistral did not release the codec encoder needed for zero-shot cloning."
        )

    def list_voices(self) -> list[str]:
        return [
            "neutral_female",
            "neutral_male",
            "cheerful_female",
            "casual_female",
            "casual_male",
            "fr_male",
            "fr_female",
            "de_male",
            "de_female",
            "es_male",
            "es_female",
            "it_male",
            "it_female",
            "pt_male",
            "pt_female",
            "nl_male",
            "nl_female",
            "ar_male",
            "hi_male",
            "hi_female",
        ]
