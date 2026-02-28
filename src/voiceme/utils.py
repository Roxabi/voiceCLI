from datetime import datetime
from pathlib import Path

OUTPUT_DIR = Path("output")


def default_output_path(prefix: str = "voiceme") -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return OUTPUT_DIR / f"{prefix}_{ts}.wav"
