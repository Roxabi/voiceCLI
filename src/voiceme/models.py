"""Model metadata and HuggingFace cache utilities."""

from __future__ import annotations

from pathlib import Path

from huggingface_hub.constants import HF_HUB_CACHE

# repo_id → (display_name, estimated_size_gb)
MODEL_REGISTRY: dict[str, tuple[str, float]] = {
    "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice": ("Qwen3 TTS CustomVoice", 3.5),
    "Qwen/Qwen3-TTS-12Hz-1.7B-Base": ("Qwen3 TTS Base (clone)", 3.5),
    "ResembleAI/chatterbox": ("Chatterbox (turbo + multilingual)", 1.8),
}


def hf_cache_dir() -> Path:
    """Return the HuggingFace Hub cache directory, respecting env vars."""
    return Path(HF_HUB_CACHE)


def _model_cache_path(repo_id: str) -> Path:
    """Return the expected cache path for a model repo."""
    # HF stores as models--Org--Name
    safe_name = repo_id.replace("/", "--")
    return hf_cache_dir() / f"models--{safe_name}"


def is_model_cached(repo_id: str) -> bool:
    """Check if a model has been downloaded (snapshots dir exists and is non-empty)."""
    snapshots = _model_cache_path(repo_id) / "snapshots"
    if not snapshots.exists():
        return False
    return any(snapshots.iterdir())


def cached_model_size_gb(repo_id: str) -> float | None:
    """Return actual disk size in GB of a cached model, or None if not cached."""
    cache_path = _model_cache_path(repo_id)
    if not cache_path.exists():
        return None
    total = sum(f.stat().st_size for f in cache_path.rglob("*") if f.is_file())
    return round(total / (1024**3), 2)


def warn_if_first_download(repo_id: str) -> None:
    """Print a warning if the model is not yet cached (first download)."""
    if is_model_cached(repo_id):
        return
    meta = MODEL_REGISTRY.get(repo_id)
    if meta:
        name, size_gb = meta
        print(f"\n[!] First-time download: {name} (~{size_gb} GB)")
    else:
        print(f"\n[!] First-time download: {repo_id}")
    print("    This may take several minutes depending on your connection.")
    print("    The model will be cached for future use.\n")
