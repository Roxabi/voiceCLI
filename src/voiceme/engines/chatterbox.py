import re

import numpy as np
import soundfile as sf
from pathlib import Path

from voiceme.engine import TTSEngine
from voiceme.utils import resolve_language as _resolve_language


def _split_sentences(text: str, max_chars: int = 250) -> list[str]:
    """Split text into sentence-sized chunks for Chatterbox (avoids 40s cutoff)."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) <= max_chars:
            current += (" " + s if current else s)
        else:
            if current:
                chunks.append(current.strip())
            current = s
    if current:
        chunks.append(current.strip())
    return chunks


class ChatterboxEngine(TTSEngine):
    name = "chatterbox"

    def __init__(self):
        self._model = None

    def _load_model(self):
        if self._model is None:
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS

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
        chunks = _split_sentences(text)
        wavs = []
        for i, chunk in enumerate(chunks):
            print(f"  [{i + 1}/{len(chunks)}] {chunk[:60]}...")
            kw = {**gen_kwargs, "text": chunk}
            wav = model.generate(**kw)
            wavs.append(wav.squeeze().cpu().numpy())
        return np.concatenate(wavs)

    def generate(self, text: str, voice: str | None, output_path: Path, **kwargs) -> Path:
        language = _resolve_language(kwargs.get("language", "English"))
        exaggeration = kwargs.get("exaggeration", 0.5)
        cfg_weight = kwargs.get("cfg_weight", 0.5)
        gen_kwargs = dict(
            language_id=language,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
        )

        audio = self._generate_chunked(text, **gen_kwargs)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output_path), audio, self._load_model().sr)
        return output_path

    def clone(
        self, text: str, ref_audio: Path, output_path: Path, ref_text: str | None = None, **kwargs
    ) -> Path:
        language = _resolve_language(kwargs.get("language", "English"))
        exaggeration = kwargs.get("exaggeration", 0.5)
        # Default cfg_weight=0.0 for cross-language cloning to reduce accent bleed
        cfg_weight = kwargs.get("cfg_weight", 0.0)
        gen_kwargs = dict(
            audio_prompt_path=str(ref_audio),
            language_id=language,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
        )

        audio = self._generate_chunked(text, **gen_kwargs)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output_path), audio, self._load_model().sr)
        return output_path

    def list_voices(self) -> list[str]:
        return ["default"]
