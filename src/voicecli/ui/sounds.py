"""UI sound playback via paplay (non-blocking).

Sounds are loaded from the voicecli assets directory.
"""

from __future__ import annotations

import sys
from pathlib import Path


def play_ui_sound(name: str) -> None:
    """Play a UI sound from the assets directory via paplay (non-blocking)."""
    import subprocess

    assets_local = Path(__file__).parent / "assets"
    assets_installed = (
        Path(sys.executable).parent.parent
        / "lib"
        / "python3.12"
        / "site-packages"
        / "voicecli"
        / "assets"
    )
    path = assets_local / name if assets_local.exists() else assets_installed / name
    if path.exists():
        subprocess.Popen(
            ["paplay", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
