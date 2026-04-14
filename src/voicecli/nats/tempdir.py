"""Per-request temp file management under /tmp/voicecli-nats/."""

from __future__ import annotations

import contextlib
from pathlib import Path

TEMP_ROOT = Path("/tmp/voicecli-nats")


def scoped_path(request_id: str, ext: str) -> Path:
    """Per-request unique temp path under TEMP_ROOT. Creates the parent if missing."""
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    return TEMP_ROOT / f"{request_id}.{ext.lstrip('.')}"


def cleanup(p: Path) -> None:
    """Remove p if it exists. Idempotent — silently ignores FileNotFoundError."""
    with contextlib.suppress(FileNotFoundError):
        p.unlink()
