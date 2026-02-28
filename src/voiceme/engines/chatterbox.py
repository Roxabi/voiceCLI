import torchaudio as ta
from pathlib import Path

from voiceme.engine import TTSEngine


class ChatterboxEngine(TTSEngine):
    name = "chatterbox"

    def __init__(self):
        self._model = None

    def _load_model(self):
        if self._model is None:
            from chatterbox.tts import ChatterboxTTS

            print("[chatterbox] Loading model...")
            self._model = ChatterboxTTS.from_pretrained(device="cuda")
            print("[chatterbox] Model loaded.")
        return self._model

    def generate(self, text: str, voice: str | None, output_path: Path, **kwargs) -> Path:
        model = self._load_model()

        gen_kwargs = dict(text=text)
        exaggeration = kwargs.get("exaggeration", 0.5)
        cfg_weight = kwargs.get("cfg_weight", 0.5)
        gen_kwargs["exaggeration"] = exaggeration
        gen_kwargs["cfg_weight"] = cfg_weight

        wav = model.generate(**gen_kwargs)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        ta.save(str(output_path), wav, model.sr)
        return output_path

    def clone(
        self, text: str, ref_audio: Path, output_path: Path, ref_text: str | None = None, **kwargs
    ) -> Path:
        model = self._load_model()

        gen_kwargs = dict(text=text, audio_prompt_path=str(ref_audio))
        exaggeration = kwargs.get("exaggeration", 0.5)
        cfg_weight = kwargs.get("cfg_weight", 0.5)
        gen_kwargs["exaggeration"] = exaggeration
        gen_kwargs["cfg_weight"] = cfg_weight

        wav = model.generate(**gen_kwargs)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        ta.save(str(output_path), wav, model.sr)
        return output_path

    def list_voices(self) -> list[str]:
        return ["default"]
