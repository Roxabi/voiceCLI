"""Model registry with LRU eviction for VRAM management.

Provides a singleton cache for TTSEngine instances with automatic
eviction of least-recently-used engines when VRAM is insufficient.

ADR-002: VRAM model management for NATS TTS satellite.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voicecli.engine import TTSEngine

log = logging.getLogger(__name__)

# VRAM status thresholds (shared with tts_adapter)
VRAM_OK_MB = 4096
VRAM_CONSTRAINED_MB = 1024


class InsufficientVRAMError(RuntimeError):
    """Raised when VRAM is insufficient even after evicting all cached engines."""


class ModelRegistry:
    """Thread-safe LRU cache for TTSEngine instances with VRAM-aware eviction.

    Attributes:
        _cache: OrderedDict mapping engine names to instances (LRU order).
        _max_cached: Maximum number of engines to cache before eviction.
        _lock: Thread lock for concurrent access safety.
        _loading: Dict of engine names to locks for in-progress loads.
    """

    def __init__(self, max_cached: int = 2) -> None:
        """Initialize the registry.

        Args:
            max_cached: Maximum engines to keep in cache (default 2).

        Raises:
            ValueError: If max_cached < 1.
        """
        if max_cached < 1:
            raise ValueError(f"max_cached must be >= 1, got {max_cached}")
        self._cache: OrderedDict[str, TTSEngine] = OrderedDict()
        self._max_cached = max_cached
        self._lock = threading.Lock()
        self._loading: dict[str, threading.Lock] = {}

    def configure(self, max_cached: int) -> None:
        """Configure the registry. Thread-safe.

        Args:
            max_cached: Maximum engines to keep in cache.

        Raises:
            ValueError: If max_cached < 1.
        """
        if max_cached < 1:
            raise ValueError(f"max_cached must be >= 1, got {max_cached}")
        with self._lock:
            self._max_cached = max_cached

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
            # Get or create per-engine loading lock
            if name not in self._loading:
                self._loading[name] = threading.Lock()
            load_lock = self._loading[name]

        # Acquire per-engine lock to serialize loads
        with load_lock:
            # Double-check after acquiring load lock (another thread may have loaded)
            with self._lock:
                if name in self._cache:
                    self._cache.move_to_end(name)
                    return self._cache[name]

            # Cache miss - validate engine exists
            from voicecli.engine import _get_registry

            engines = _get_registry()
            if name not in engines:
                raise ValueError(f"Unknown engine '{name}'. Available: {list(engines.keys())}")

            # Load engine outside main lock (30s operation)
            engine = engines[name]()

            # Check VRAM and evict if needed
            # Note: load_lock is released on exception, preventing deadlock
            self._ensure_vram(name)

            # Insert into cache
            with self._lock:
                if name not in self._cache:  # Triple-check after lock
                    self._cache[name] = engine
                    # Cleanup loading lock after successful load
                    self._loading.pop(name, None)
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
        except Exception as e:
            log.debug("VRAM check failed: %s", e)
            return 0

    def vram_status(self) -> str:
        """Return VRAM status string based on free memory.

        Returns:
            "ok" if > 4GB free, "constrained" if 1-4GB, "critical" if < 1GB.
        """
        free_mb = self.vram_free_mb()
        if free_mb >= VRAM_OK_MB:
            return "ok"
        elif free_mb >= VRAM_CONSTRAINED_MB:
            return "constrained"
        else:
            return "critical"

    def _ensure_vram(self, required: str) -> None:
        """Evict until enough VRAM for required engine.

        Args:
            required: Engine name that needs to be loaded.

        Raises:
            InsufficientVRAMError: Not enough VRAM even after full eviction.
        """
        from voicecli.engine import VRAM_REQUIRED_GB, VRAM_REQUIRED_GB_DEFAULT

        required_gb = VRAM_REQUIRED_GB.get(required, VRAM_REQUIRED_GB_DEFAULT)

        # Skip VRAM check if CUDA unavailable (returns 0 from vram_free_mb)
        # This allows tests and CPU-only environments to work
        if self.vram_free_mb() == 0:
            return

        # Evict until we have enough VRAM
        # Hold lock during entire eviction loop to prevent race conditions
        while not self._has_vram(required_gb):
            with self._lock:
                if not self._cache:
                    raise InsufficientVRAMError(
                        f"Not enough VRAM for '{required}' even after evicting all engines. "
                        f"Need {required_gb:.1f} GB."
                    )
                # Pop oldest item while holding lock
                _, engine = self._cache.popitem(last=False)
            # Release engine VRAM outside lock
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
