"""Blobstore-backed sample resolution for NATS clone synthesis."""

from __future__ import annotations

from pathlib import Path

from voicecli.core.sample_catalog import (
    BLOB_SOURCE,
    CACHE_DIR,
    SAMPLES_DIR,
    load_catalog,
    local_sample_path,
    register_sample,
)


def _cache_path(store_key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{store_key}.wav"


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
