"""Real-time mic-to-text using Kyutai STT."""

import subprocess
import sys
import tempfile
from pathlib import Path

MODELS = {
    "1b": "kyutai/stt-1b-en_fr-trfs",  # EN + FR, 0.5s latency
    "2.6b": "kyutai/stt-2.6b-en-trfs",  # EN only, higher quality
}
DEFAULT_MODEL = "1b"

_pipeline_cache: dict[str, object] = {}


def _load_pipeline(model: str):
    if model not in _pipeline_cache:
        import torch
        from transformers import pipeline

        model_id = MODELS[model]
        print(f"[stt] Loading Kyutai {model} ({model_id})...")
        _pipeline_cache[model] = pipeline(
            "automatic-speech-recognition",
            model=model_id,
            device="cuda",
            torch_dtype=torch.float16,
        )
        print("[stt] Model loaded.")
    return _pipeline_cache[model]


def _record_chunk(duration: float = 3.0, samplerate: int = 16000) -> Path:
    """Record a short audio chunk from mic via PulseAudio."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        subprocess.run(
            [
                "parecord",
                "--channels=1",
                f"--rate={samplerate}",
                "--format=s16le",
                "--file-format=wav",
                str(tmp.name),
            ],
            timeout=duration,
        )
    except subprocess.TimeoutExpired:
        pass  # expected — this is how we stop after the set duration
    return Path(tmp.name)


def listen_loop(model: str = DEFAULT_MODEL, chunk_duration: float = 3.0):
    """Record-then-transcribe loop. Prints text as it arrives. Ctrl+C to stop."""
    pipe = _load_pipeline(model)
    print("Listening... (Ctrl+C to stop)\n")
    try:
        while True:
            chunk_path = _record_chunk(duration=chunk_duration)
            try:
                result = pipe(str(chunk_path))
                text = result["text"].strip()
                if text:
                    sys.stdout.write(text + " ")
                    sys.stdout.flush()
            finally:
                chunk_path.unlink(missing_ok=True)
    except KeyboardInterrupt:
        print("\n\nStopped.")
