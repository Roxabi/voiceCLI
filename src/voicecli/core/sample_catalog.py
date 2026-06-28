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


async def resolve_sample_path(sample_id: str) -> Path:
    """Resolve clone reference audio: cache → blobstore GET → legacy local WAV."""
    catalog = load_catalog()
    meta = catalog.get("samples", {}).get(sample_id)
    if not isinstance(meta, dict):
        raise FileNotFoundError(f"Unknown sample: {sample_id}")

    store_key = meta.get("store_key")
    if store_key:
        cached = _cache_path(store_key)
        if cached.exists():
            return cached
        from voicecli.adapters.nats.blobs import get_blobstore  # noqa: PLC0415

        wav_bytes = await get_blobstore().get(store_key)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(wav_bytes)
        cached.chmod(0o600)
        return cached

    local = local_sample_path(sample_id)
    if local is not None:
        return local
    raise FileNotFoundError(
        f"Sample {sample_id!r} has no store_key and no local file under {SAMPLES_DIR}"
    )


async def publish_sample_to_blobstore(sample_id: str) -> str:
    """Upload local WAV to blobstore and persist store_key in catalogue."""
    local = local_sample_path(sample_id)
    if local is None:
        raise FileNotFoundError(f"Sample not found: {sample_id}")
    from voicecli.adapters.nats.blobs import get_blobstore  # noqa: PLC0415

    ref = await get_blobstore().put(
        local.read_bytes(),
        mime="audio/wav",
        source=BLOB_SOURCE,
        filename=sample_id,
    )
    register_sample(sample_id, store_key=ref.store_key)
    return ref.store_key
