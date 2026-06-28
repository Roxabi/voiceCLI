"""Sample catalogue — semantic index (sample_id → store_key) with blobstore cache."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from voicecli.core.samples import SAMPLES_DIR, ensure_dir, list_samples

log = logging.getLogger(__name__)

CATALOG_PATH = SAMPLES_DIR / "catalog.json"
CACHE_DIR = Path.home() / ".cache" / "voicecli" / "samples"
BLOB_SOURCE = "voicecli-sample"


def _empty_catalog() -> dict[str, Any]:
    return {"version": 1, "samples": {}}


def load_catalog() -> dict[str, Any]:
    """Load catalogue from disk; bootstrap entries for local WAVs without records."""
    ensure_dir()
    if not CATALOG_PATH.exists():
        catalog = _empty_catalog()
    else:
        try:
            catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("sample_catalog: corrupt catalog.json — resetting: %s", exc)
            catalog = _empty_catalog()
    samples: dict[str, Any] = catalog.setdefault("samples", {})
    for name in list_samples():
        samples.setdefault(name, {"store_key": None, "filename": name})
    return catalog


def save_catalog(catalog: dict[str, Any]) -> None:
    ensure_dir()
    CATALOG_PATH.write_text(
        json.dumps(catalog, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    CATALOG_PATH.chmod(0o600)


def catalog_revision(catalog: dict[str, Any] | None = None) -> str:
    """Stable hash for heartbeat / dashboard cache invalidation."""
    data = json.dumps(catalog or load_catalog(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def list_catalog_entries() -> list[dict[str, Any]]:
    catalog = load_catalog()
    entries: list[dict[str, Any]] = []
    for sample_id, meta in sorted(catalog.get("samples", {}).items()):
        if not isinstance(meta, dict):
            continue
        store_key = meta.get("store_key")
        entries.append(
            {
                "id": sample_id,
                "store_key": store_key,
                "filename": meta.get("filename", sample_id),
                "cached": _is_cached(store_key) or (SAMPLES_DIR / sample_id).exists(),
            }
        )
    return entries


def _is_cached(store_key: str | None) -> bool:
    if not store_key:
        return False
    return (CACHE_DIR / f"{store_key}.wav").exists()


def _cache_path(store_key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{store_key}.wav"


def register_sample(sample_id: str, *, store_key: str | None = None) -> None:
    catalog = load_catalog()
    catalog["samples"][sample_id] = {
        "store_key": store_key,
        "filename": sample_id,
    }
    save_catalog(catalog)


def remove_sample_from_catalog(sample_id: str) -> None:
    catalog = load_catalog()
    meta = catalog.get("samples", {}).pop(sample_id, None)
    if isinstance(meta, dict):
        store_key = meta.get("store_key")
        if store_key:
            cache = _cache_path(store_key)
            cache.unlink(missing_ok=True)
    save_catalog(catalog)


def local_sample_path(sample_id: str) -> Path | None:
    path = SAMPLES_DIR / sample_id
    return path if path.exists() else None
