"""Per-request temp file management under /tmp/voicecli-nats/."""

from __future__ import annotations

import contextlib
from pathlib import Path

TEMP_ROOT = Path("/tmp/voicecli-nats")


def scoped_path(request_id: str, ext: str) -> Path:
    """Per-request unique temp path under TEMP_ROOT. Creates the parent if missing.

    Raises ValueError if *request_id* escapes TEMP_ROOT after path resolution
    (defense-in-depth against path traversal).
    """
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    p = TEMP_ROOT.joinpath(f"{request_id}.{ext.lstrip('.')}").resolve()
    root_resolved = TEMP_ROOT.resolve()
    if not p.is_relative_to(root_resolved):
        raise ValueError(f"request_id escapes temp root: {request_id!r}")
    return p


def cleanup(p: Path) -> None:
    """Remove p if it exists. Idempotent — silently ignores FileNotFoundError."""
    with contextlib.suppress(FileNotFoundError):
        p.unlink()
