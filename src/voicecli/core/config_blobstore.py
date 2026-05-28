"""Blobstore config loading — extracted from config.py to keep file under 300 lines."""

from __future__ import annotations

import os
from typing import Any
from pathlib import Path

from voicecli.core.config import _find_config

_KNOWN_BLOBSTORE: dict[str, type] = {
    "backend": str,
    "url": str,
    "bearer_token": str,
}


def load_blobstore_config(config: Path | None = None) -> dict:
    """Load the ``[blobstore]`` table from voicecli.toml.

    Returns empty dict when no config is found or the table is absent.
    """
    path = config if config is not None else _find_config()
    if path is None:
        return {}
    with open(path, "rb") as f:
        data = __import__("tomllib").load(f)
    raw = data.get("blobstore", {})
    result: dict[str, Any] = {}
    for key, expected_type in _KNOWN_BLOBSTORE.items():
        if key in raw:
            try:
                result[key] = expected_type(raw[key])
            except (ValueError, TypeError):
                pass
    return result


def apply_blobstore_env_from_config(config: Path | None = None) -> None:
    """Populate ``BLOBSTORE_*`` env vars from ``[blobstore]`` toml when unset.

    Env vars take precedence (so wrappers, CI, and ad-hoc overrides keep
    working). Call this at the entrypoint of any blobstore-using command before
    code reads ``os.environ``.
    """
    cfg = load_blobstore_config(config)
    if "BLOBSTORE_BACKEND" not in os.environ:
        backend = cfg.get("backend")
        if isinstance(backend, str) and backend:
            os.environ["BLOBSTORE_BACKEND"] = backend
    if "BLOBSTORE_URL" not in os.environ:
        url = cfg.get("url")
        if isinstance(url, str) and url:
            os.environ["BLOBSTORE_URL"] = url
    if "BLOBSTORE_BEARER_TOKEN" not in os.environ:
        token = cfg.get("bearer_token")
        if isinstance(token, str) and token:
            os.environ["BLOBSTORE_BEARER_TOKEN"] = token
