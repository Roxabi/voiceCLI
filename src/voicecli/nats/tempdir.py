"""Per-request temp file management under /tmp/voicecli-nats/."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

TEMP_ROOT = Path("/tmp/voicecli-nats")


def scoped_path(request_id: str, ext: str) -> Path:
    """Per-request unique temp path under TEMP_ROOT. Creates the parent if missing.

    Raises ValueError if *request_id* escapes TEMP_ROOT after path resolution
    (defense-in-depth against path traversal). Null bytes are rejected with a
    custom message before any filesystem interaction, so the error path matches
    the other traversal cases instead of relying on CPython's OS-layer message.
    """
    if "\x00" in request_id:
        raise ValueError(f"escapes temp root: {request_id!r} (null byte)")
    # issue #60: tighten umask at the abstraction boundary so every nats-serve
    # subcommand (and any future caller) inherits 0o600 file writes without
    # having to remember the call. Idempotent.
    os.umask(0o077)
    TEMP_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Python's mkdir(mode=..., exist_ok=True) only applies mode at creation time;
    # a dir that already exists with looser perms is silently reused. Enforce mode
    # unconditionally so a prior install or a pre-created world-readable dir cannot
    # leak per-request audio to other local users.
    TEMP_ROOT.chmod(0o700)
    p = TEMP_ROOT.joinpath(f"{request_id}.{ext.lstrip('.')}").resolve()
    root_resolved = TEMP_ROOT.resolve()
    if not p.is_relative_to(root_resolved):
        raise ValueError(f"request_id escapes temp root: {request_id!r}")
    return p


def cleanup(p: Path) -> None:
    """Remove p if it exists. Idempotent — silently ignores FileNotFoundError."""
    with contextlib.suppress(FileNotFoundError):
        p.unlink()
