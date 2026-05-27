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
backend raise ``ValueError`` immediately so misconfigured deployments fail fast.
"""

from __future__ import annotations

import os

from roxabi_blobs import HttpBlobStore

_INSTANCE: HttpBlobStore | None = None


def get_blobstore() -> HttpBlobStore:
    """Return the singleton ``HttpBlobStore`` client, creating it on first call.

    Reads configuration from environment variables:

    - ``BLOBSTORE_BACKEND`` — must be ``"http"`` (ValueError otherwise).
    - ``BLOBSTORE_URL``     — base URL of the lyra-hub HTTP BlobStore service.
    - ``BLOBSTORE_BEARER_TOKEN`` — bearer token for HTTP authentication.

    Raises:
        ValueError: if ``BLOBSTORE_BACKEND`` is not ``"http"``.
        KeyError: if ``BLOBSTORE_URL`` or ``BLOBSTORE_BEARER_TOKEN`` are absent.
    """
    global _INSTANCE
    if _INSTANCE is None:
        backend = os.environ.get("BLOBSTORE_BACKEND", "")
        if backend != "http":
            raise ValueError(
                f"BLOBSTORE_BACKEND must be 'http' for voicecli workers (got {backend!r}); "
                "FS-direct backend forbidden per ADR-068"
            )
        url = os.environ["BLOBSTORE_URL"]
        token = os.environ["BLOBSTORE_BEARER_TOKEN"]
        _INSTANCE = HttpBlobStore(base_url=url, token=token)
    return _INSTANCE
