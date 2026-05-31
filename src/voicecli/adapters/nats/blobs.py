"""HttpBlobStore factory for NATS adapters — module-level lazy singleton.

No GPU / VRAM allocation: this module performs only CPU + HTTP network init.
The singleton is process-local; concurrent NATS satellites that happen to share
a process get the same client instance (safe — HttpBlobStore is stateless aside
from the httpx.AsyncClient which is thread-safe for concurrent async usage).

ADR-068 constraint
------------------
Workers MUST use the HTTP backend regardless of physical co-location with the
lyra-hub BlobStore service (even on M₁ where both run on the same host).  The
HTTP abstraction preserves the contract when worker processes move (e.g. STT/TTS
→ M₂ for GPU isolation) without re-wiring the code.  Requests for a ``flat-fs``
backend raise ``BlobstoreConfigError`` immediately so misconfigured deployments
fail fast.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from pydantic import ValidationError
from roxabi_blobs import HttpBlobStore
from roxabi_contracts.blob_ref import BlobRef as ContractsBlobRef

_INSTANCE: HttpBlobStore | None = None
_LOCK = threading.Lock()

# Sentinel store_key emitted by adapters before BlobStore ingest lands.
# Workers receiving a BlobRef with this store_key must fall back to the
# legacy platform fetch path instead of calling blob_store.get(store_key).
_PENDING_STORE_KEY = "__pending__"


class BlobstoreConfigError(RuntimeError):
    """Raised when blobstore configuration is invalid or missing.

    Distinct from network/runtime BlobStore errors — callers can catch this to
    surface a structured ``blobstore_not_configured`` error code separate from
    fetch/put failures, and adapters log it as ``blobstore_init_failed``.
    """


class BlobRefValidationError(BlobstoreConfigError):
    """Raised when a blob_ref field fails validation (e.g. invalid store_key)."""


def get_blobstore() -> HttpBlobStore:
    """Return the singleton ``HttpBlobStore`` client, creating it on first call.

    Reads configuration from environment variables:

    - ``BLOBSTORE_BACKEND`` — must be ``"http"``.
    - ``BLOBSTORE_URL``     — base URL of the lyra-hub HTTP BlobStore service.
    - ``BLOBSTORE_BEARER_TOKEN`` — bearer token for HTTP authentication.

    Raises:
        BlobstoreConfigError: if any required env var is missing/invalid.
    """
    global _INSTANCE
    if _INSTANCE is None:
        with _LOCK:
            if _INSTANCE is None:
                backend = os.environ.get("BLOBSTORE_BACKEND", "")
                if backend != "http":
                    raise BlobstoreConfigError(
                        f"BLOBSTORE_BACKEND must be 'http' for voicecli workers (got {backend!r}); "
                        "FS-direct backend forbidden per ADR-068"
                    )
                try:
                    url = os.environ["BLOBSTORE_URL"]
                    token = os.environ["BLOBSTORE_BEARER_TOKEN"]
                except KeyError as e:
                    raise BlobstoreConfigError(f"required env var not set: {e.args[0]}") from e
                _INSTANCE = HttpBlobStore(base_url=url, token=token)
    return _INSTANCE


def reset_blobstore_for_tests() -> None:
    """Reset the singleton instance for test isolation.

    Safe to call between tests; the next ``get_blobstore()`` call will
    re-initialize the instance from environment variables.
    """
    global _INSTANCE
    with _LOCK:
        _INSTANCE = None


def blob_ref_to_contract(ref: Any) -> ContractsBlobRef:
    """Bridge ``roxabi_blobs.BlobRef`` → ``roxabi_contracts.BlobRef``.

    Canonical converter via ``roxabi_contracts.BlobRef.from_store_ref`` —
    drops storage-only fields (``id``, ``is_sentinel``) and carries every
    wire field (incl. ``created_at``) through verbatim.

    A ``pydantic.ValidationError`` from ``from_store_ref`` signals field-set
    drift between the storage and wire schemas.  It is re-raised as a typed
    ``BlobRefValidationError`` so callers can distinguish contract-level
    mismatches from generic runtime errors.
    """
    store_key = getattr(ref, "store_key", "")
    if store_key == _PENDING_STORE_KEY:
        raise BlobRefValidationError(f"Pending store_key not allowed: {store_key!r}")
    try:
        return ContractsBlobRef.from_store_ref(ref)
    except ValidationError as e:
        raise BlobRefValidationError(str(e)) from e
