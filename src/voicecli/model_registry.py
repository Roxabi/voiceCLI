"""Model registry with LRU eviction for VRAM management.

Provides a singleton cache for TTSEngine instances with automatic
eviction of least-recently-used engines when VRAM is insufficient.

ADR-002: VRAM model management for NATS TTS satellite.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voicecli.engine import TTSEngine


class InsufficientVRAMError(RuntimeError):
    """Raised when VRAM is insufficient even after evicting all cached engines."""


class ModelRegistry:
    """Thread-safe LRU cache for TTSEngine instances with VRAM-aware eviction.

    Attributes:
        _cache: OrderedDict mapping engine names to instances (LRU order).
        _max_cached: Maximum number of engines to cache before eviction.
        _lock: Thread lock for concurrent access safety.
    """

    def __init__(self, max_cached: int = 2) -> None:
        """Initialize the registry.

        Args:
            max_cached: Maximum engines to keep in cache (default 2).
        """
        self._cache: OrderedDict[str, TTSEngine] = OrderedDict()
        self._max_cached = max_cached
        self._lock = threading.Lock()

    def get(self, name: str) -> TTSEngine:
        """Get engine by name. Loads if not cached. Thread-safe.

        Args:
            name: Engine name (e.g., "qwen-fast", "chatterbox").

        Returns:
            TTSEngine instance (cached or newly loaded).

        Raises:
            ValueError: Unknown engine name.
            InsufficientVRAMError: Not enough VRAM even after full eviction.
        """
        # Cache hit - fast path with lock
        with self._lock:
            if name in self._cache:
                self._cache.move_to_end(name)  # O(1) LRU touch
                return self._cache[name]

        # Cache miss - validate engine exists
        from voicecli.engine import _get_registry

        engines = _get_registry()
        if name not in engines:
            raise ValueError(f"Unknown engine '{name}'. Available: {list(engines.keys())}")

        # Check VRAM and evict if needed
        self._ensure_vram(name)

        # Load engine outside lock (30s operation)
        engine = engines[name]()

        # Insert into cache
        with self._lock:
            if name not in self._cache:  # Double-check after lock
                self._cache[name] = engine
            self._cache.move_to_end(name)

        return engine

    def loaded_engines(self) -> list[str]:
        """Return list of currently cached engine names in LRU order."""
        with self._lock:
            return list(self._cache.keys())

    def evict(self, name: str) -> None:
        """Evict specific engine from cache.

        Args:
            name: Engine name to evict.

        Note:
            Safe to call even if engine not in cache (no-op).
        """
        with self._lock:
            if name not in self._cache:
                return
            engine = self._cache.pop(name)
        self._release_engine(engine)

    def vram_free_mb(self) -> int:
        """Return free VRAM in MB.

        Returns:
            Free VRAM in megabytes, or 0 if CUDA unavailable.
        """
        try:
            import torch

            if not torch.cuda.is_available():
                return 0
            free_bytes, _ = torch.cuda.mem_get_info()
            return free_bytes // (1024 * 1024)
        except Exception:
            return 0

    def _touch(self, name: str) -> None:
        """Move engine to end of LRU order (most recently used).

        Args:
            name: Engine name currently in cache.
        """
        self._cache.move_to_end(name)

    def _ensure_vram(self, required: str) -> None:
        """Evict until enough VRAM for required engine.

        Args:
            required: Engine name that needs to be loaded.

        Raises:
            InsufficientVRAMError: Not enough VRAM even after full eviction.
        """
        from voicecli.engine import VRAM_REQUIRED_GB, VRAM_REQUIRED_GB_DEFAULT

        required_gb = VRAM_REQUIRED_GB.get(required, VRAM_REQUIRED_GB_DEFAULT)

        # Evict until we have enough VRAM
        while not self._has_vram(required_gb):
            with self._lock:
                if not self._cache:
                    raise InsufficientVRAMError(
                        f"Not enough VRAM for '{required}' even after evicting all engines. "
                        f"Need {required_gb:.1f} GB."
                    )
            self._evict_oldest()

    def _evict_oldest(self) -> None:
        """Evict least-recently-used engine from cache.

        Note:
            Does nothing if cache is empty.
        """
        with self._lock:
            if not self._cache:
                return
            _, engine = self._cache.popitem(last=False)
        self._release_engine(engine)

    def _has_vram(self, required_gb: float) -> bool:
        """Check if free VRAM is sufficient.

        Args:
            required_gb: Required VRAM in GB.

        Returns:
            True if free VRAM >= required, False otherwise.
        """
        free_mb = self.vram_free_mb()
        return free_mb >= required_gb * 1024

    def _release_engine(self, engine: TTSEngine) -> None:
        """Release engine's VRAM and delete it.

        Args:
            engine: Engine instance to release.
        """
        import gc

        # Clear model references (attributes set dynamically on engine instances)
        for attr in ("_model", "_clone_model"):
            if hasattr(engine, attr):
                setattr(engine, attr, None)
        del engine

        # Force VRAM cleanup
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


# Singleton instance
model_registry = ModelRegistry()
