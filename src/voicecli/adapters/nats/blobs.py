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
from pathlib import Path
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


def _resolve_bearer_token() -> str:
    """Read bearer token from ``BLOBSTORE_BEARER_TOKEN_PATH`` or env.

    Token path is preferred on M₁ workers (shared ``~/.roxabi/factory/blobstore.tok``
    bind-mount). ``BLOBSTORE_BEARER_TOKEN`` remains for M₂ clients and wrappers.
    Read once at singleton init — restart required after rotation (factory parity).
    """
    path = os.environ.get("BLOBSTORE_BEARER_TOKEN_PATH", "").strip()
    if path:
        try:
            token = Path(path).read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise BlobstoreConfigError(
                f"BLOBSTORE_BEARER_TOKEN_PATH file not found: {path}"
            ) from exc
        except OSError as exc:
            raise BlobstoreConfigError(
                f"BLOBSTORE_BEARER_TOKEN_PATH unreadable: {path}: {exc}"
            ) from exc
        if not token:
            raise BlobstoreConfigError(f"BLOBSTORE_BEARER_TOKEN_PATH is empty: {path}")
        return token
    try:
        token = os.environ["BLOBSTORE_BEARER_TOKEN"].strip()
    except KeyError as exc:
        raise BlobstoreConfigError(
            "required env var not set: BLOBSTORE_BEARER_TOKEN (or set BLOBSTORE_BEARER_TOKEN_PATH)"
        ) from exc
    if not token:
        raise BlobstoreConfigError("BLOBSTORE_BEARER_TOKEN is empty")
    return token


def get_blobstore() -> HttpBlobStore:
    """Return the singleton ``HttpBlobStore`` client, creating it on first call.

    Reads configuration from environment variables:

    - ``BLOBSTORE_BACKEND`` — must be ``"http"``.
    - ``BLOBSTORE_URL``     — base URL of the lyra-hub HTTP BlobStore service.
    - ``BLOBSTORE_BEARER_TOKEN_PATH`` — file with bearer token (preferred on M₁).
    - ``BLOBSTORE_BEARER_TOKEN`` — inline bearer token (M₂ clients, legacy).

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
                except KeyError as e:
                    raise BlobstoreConfigError(f"required env var not set: {e.args[0]}") from e
                token = _resolve_bearer_token()
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
