import soundfile as sf
import torch
from pathlib import Path

from voiceme.engine import TTSEngine

SPEAKERS = [
    "Vivian", "Serena", "Uncle_Fu", "Dylan",
    "Eric", "Ryan", "Aiden", "Ono_Anna", "Sohee",
]

DEFAULT_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
CLONE_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"


class QwenEngine(TTSEngine):
    name = "qwen"

    def __init__(self):
        self._model = None
        self._clone_model = None

    def _load_model(self):
        if self._model is None:
            from qwen_tts import Qwen3TTSModel

            print(f"[qwen] Loading {DEFAULT_MODEL}...")
            kwargs = {"device_map": "cuda:0", "dtype": torch.bfloat16}
            try:
                kwargs["attn_implementation"] = "flash_attention_2"
                self._model = Qwen3TTSModel.from_pretrained(DEFAULT_MODEL, **kwargs)
            except Exception:
                kwargs.pop("attn_implementation", None)
                self._model = Qwen3TTSModel.from_pretrained(DEFAULT_MODEL, **kwargs)
            print("[qwen] Model loaded.")
        return self._model

    def _load_clone_model(self):
        if self._clone_model is None:
            from qwen_tts import Qwen3TTSModel

            print(f"[qwen] Loading {CLONE_MODEL}...")
            kwargs = {"device_map": "cuda:0", "dtype": torch.bfloat16}
            try:
                kwargs["attn_implementation"] = "flash_attention_2"
                self._clone_model = Qwen3TTSModel.from_pretrained(CLONE_MODEL, **kwargs)
            except Exception:
                kwargs.pop("attn_implementation", None)
                self._clone_model = Qwen3TTSModel.from_pretrained(CLONE_MODEL, **kwargs)
            print("[qwen] Clone model loaded.")
        return self._clone_model

    def generate(self, text: str, voice: str | None, output_path: Path, **kwargs) -> Path:
        model = self._load_model()
        voice = voice or "Ryan"
        if voice not in SPEAKERS:
            raise ValueError(f"Unknown voice '{voice}'. Available: {SPEAKERS}")

        language = kwargs.get("language", "English")
        instruct = kwargs.get("instruct")

        gen_kwargs = dict(text=text, language=language, speaker=voice)
        if instruct:
            gen_kwargs["instruct"] = instruct

        wavs, sr = model.generate_custom_voice(**gen_kwargs)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output_path), wavs[0], sr)
        return output_path

    def clone(
        self, text: str, ref_audio: Path, output_path: Path, ref_text: str | None = None, **kwargs
    ) -> Path:
        model = self._load_clone_model()
        language = kwargs.get("language", "English")

        gen_kwargs = dict(text=text, language=language, ref_audio=str(ref_audio))
        if ref_text:
            gen_kwargs["ref_text"] = ref_text

        wavs, sr = model.generate_voice_clone(**gen_kwargs)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output_path), wavs[0], sr)
        return output_path

    def list_voices(self) -> list[str]:
        return list(SPEAKERS)
