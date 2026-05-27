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
from typing import Any

from roxabi_blobs import HttpBlobStore
from roxabi_contracts.blob_ref import BlobRef as ContractsBlobRef

_INSTANCE: HttpBlobStore | None = None

# Producer-only fields on roxabi_blobs.BlobRef that the contract BlobRef rejects
# (extra="forbid"). Drop them when bridging to the wire payload.
_PRODUCER_ONLY_FIELDS: frozenset[str] = frozenset({"id", "is_sentinel"})


class BlobstoreConfigError(RuntimeError):
    """Raised when blobstore configuration is invalid or missing.

    Distinct from network/runtime BlobStore errors — callers can catch this to
    surface a structured ``blobstore_not_configured`` error code separate from
    fetch/put failures, and adapters log it as ``blobstore_init_failed``.
    """


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


def blob_ref_to_contract(ref: Any) -> ContractsBlobRef:
    """Bridge ``roxabi_blobs.BlobRef`` → ``roxabi_contracts.BlobRef``.

    The two packages publish distinct Pydantic models with overlapping fields.
    The contract model has ``extra="forbid"`` so producer-only fields (``id``,
    ``is_sentinel``) must be stripped before validation. Centralized here so a
    field rename or addition upstream is a one-place fix instead of N callsites.
    """
    return ContractsBlobRef.model_validate(ref.model_dump(exclude=set(_PRODUCER_ONLY_FIELDS)))
