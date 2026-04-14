"""NATS connection helper with optional nkey authentication and TLS."""

from __future__ import annotations

import logging
import os
import ssl
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

log = logging.getLogger(__name__)


def _read_nkey_seed(path: Path) -> str | None:
    """Read nkey seed from *path*.

    Returns the seed string, or ``None`` if *path* is ``None``.
    Raises ``PermissionError`` if the file has group/world access bits set —
    the message mentions "0600" to guide the user toward the correct permission.
    Raises ``FileNotFoundError`` if the path does not exist or is a symlink.

    Security (TOCTOU): the path is opened once with ``O_NOFOLLOW`` (symlinks
    rejected) and ``os.fstat`` inspects the live file descriptor so the
    permission check and the read operate on the same inode. A parent-
    directory writer cannot swap the path between stat and read.
    """
    import errno
    import stat as _stat

    path_str = str(path)
    fd = -1
    try:
        fd = os.open(path_str, os.O_RDONLY | os.O_NOFOLLOW)
        st = os.fstat(fd)
        if not _stat.S_ISREG(st.st_mode):
            raise FileNotFoundError(f"NATS nkey seed path {path_str!r} is not a file")
        mode = st.st_mode & 0o777
        # Reject any group/world access (0o077) — owner-only reads are safe
        # whether they're 0o600 (rw) or 0o400 (r-only).
        if mode & 0o077:
            raise PermissionError(
                f"NATS nkey seed {path_str!r} has unsafe permissions"
                f" {oct(mode)} — use 0600 or 0400 (group/world access forbidden)"
            )
        with os.fdopen(fd, "r") as fh:
            fd = -1  # fdopen takes ownership; prevent double-close below
            seed = fh.read().strip()
    except OSError as exc:
        if fd != -1:
            os.close(fd)
        if exc.errno in (errno.ENOENT, errno.ELOOP):
            raise FileNotFoundError(f"NATS nkey seed path {path_str!r} is not a file") from exc
        raise
    if not seed:
        raise ValueError(f"NATS nkey seed {path_str!r} is empty")
    return seed


_RESERVED_KEYS = frozenset({"nkeys_seed_str", "token", "user", "password", "tls"})


def _build_tls_context(ca_cert_path: str | None = None) -> ssl.SSLContext | None:
    """Build a TLS context from *ca_cert_path* or the ``NATS_CA_CERT`` env var.

    Returns ``None`` if no CA cert is configured (plain TCP / dev mode).
    """
    ca_path_str = ca_cert_path or os.environ.get("NATS_CA_CERT")
    if not ca_path_str:
        return None
    ca_path = Path(ca_path_str)
    if not ca_path.is_file():
        sys.exit(f"NATS_CA_CERT={ca_path_str!r} is not a file")
    ctx = ssl.create_default_context(cafile=str(ca_path))
    return ctx


def scrub_nats_url(url: str) -> str:
    """Return *url* with any embedded credentials removed.

    ``nats://user:pass@host:4222`` → ``nats://host:4222``
    """
    parsed = urlparse(url)
    if not parsed.hostname:
        return url
    clean_netloc = parsed.hostname
    if parsed.port:
        clean_netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=clean_netloc))


async def _default_error_cb(exc: Exception) -> None:
    log.error("NATS error: %s", exc)


async def _default_disconnected_cb() -> None:
    log.warning("NATS disconnected")


async def _default_reconnected_cb() -> None:
    log.info("NATS reconnected")


async def nats_connect(
    url: str,
    *,
    nkey_seed_path: Path | None = None,
    ca_cert_path: str | None = None,
    **extra: Any,
) -> "NATS":
    """Connect to NATS, optionally authenticating with an nkey seed.

    If *nkey_seed_path* is provided, reads the seed file and passes it via
    ``nkeys_seed_str``. If omitted, connects without authentication (dev mode).

    Extra keyword arguments (e.g. ``error_cb``, ``disconnected_cb``,
    ``reconnected_cb``) are forwarded to ``nats.connect()``.
    Auth-related keys (``nkeys_seed_str``, ``token``, ``user``, ``password``,
    ``tls``) are rejected — authentication is owned exclusively by this helper.
    """
    import nats as _nats  # deferred: nats-py is an optional extra

    bad = _RESERVED_KEYS & extra.keys()
    if bad:
        raise ValueError(f"nats_connect: reserved keys must not be passed via **extra: {bad}")
    kwargs: dict[str, Any] = {
        "error_cb": _default_error_cb,
        "disconnected_cb": _default_disconnected_cb,
        "reconnected_cb": _default_reconnected_cb,
        **extra,
    }
    seed: str | None = None
    if nkey_seed_path is not None:
        seed = _read_nkey_seed(nkey_seed_path)
    if seed:
        kwargs["nkeys_seed_str"] = seed
    tls_ctx = _build_tls_context(ca_cert_path)
    if tls_ctx:
        kwargs["tls"] = tls_ctx
    nc = await _nats.connect(url, **kwargs)
    if seed:
        log.info("NATS nkey auth enabled")
    if tls_ctx:
        log.info("NATS TLS enabled")
    return nc
