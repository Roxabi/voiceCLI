"""Shared fixtures for the NATS adapter test suite.

Exposes three socket-state fixtures and a re-export of the canonical FakeMsg.

Convention: every async test in this package uses **one** ``asyncio.run`` per
test function. Splitting a test across two ``asyncio.run`` calls means any
adapter-instance state set inside the first loop leaks into the second, and
any ``caplog`` capture window is split across two distinct loops. Use a
single coroutine that awaits the sequential operations instead.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

import pytest

from tests.nats._fakes import FakeMsg, bound_but_closed_socket, spawn_unix_listener
from voicecli.adapters.nats.blobs import reset_blobstore_for_tests

__all__ = [
    "FakeMsg",
    "absent_socket_path",
    "live_socket_path",
    "stale_socket_path",
]


@pytest.fixture(autouse=True)
def _reset_blobstore() -> None:
    """Reset the blobstore singleton before each test for isolation."""
    reset_blobstore_for_tests()


@pytest.fixture(autouse=True)
def _isolate_nats_env_from_user_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent the user's ``~/.voicecli/voicecli.toml`` from leaking NATS_URL
    into ``nats-serve`` CLI tests.

    ``cli_nats`` calls ``apply_nats_env_from_config()`` which backfills
    ``NATS_URL`` from the local TOML when the env var is unset. CI hosts have
    no config file so tests pass there, but dev machines with a populated
    ``[nats]`` section see the "missing NATS_URL" path silently skipped and
    the test ends up actually trying to connect to NATS.
    """
    monkeypatch.setattr("voicecli.core.config.apply_nats_env_from_config", lambda: None)


@pytest.fixture(autouse=True)
def _restore_umask() -> Generator[None, None, None]:
    """Save/restore process umask around each test.

    `test_connect` invokes `nats-serve` via CliRunner, which calls
    `os.umask(0o077)` in the worker process. That leaks into every subsequent
    test collected by the same pytest worker — any test that relies on
    `mkdir(mode=...)` landing at the requested mode breaks silently.
    """
    original = os.umask(0o022)
    os.umask(original)
    try:
        yield
    finally:
        os.umask(original)


@pytest.fixture
def absent_socket_path(tmp_path: Path) -> Path:
    """A tmp_path-scoped socket path that does not exist on disk."""
    return tmp_path / "absent.sock"


@pytest.fixture
def live_socket_path(tmp_path: Path) -> Generator[Path, None, None]:
    """A tmp_path-scoped socket path with a live AF_UNIX listener accepting."""
    sock_path = tmp_path / "live.sock"
    with spawn_unix_listener(sock_path):
        yield sock_path


@pytest.fixture
def stale_socket_path(tmp_path: Path) -> Generator[Path, None, None]:
    """A tmp_path-scoped socket path whose listener was closed without unlink.

    Connect() on the yielded path returns ECONNREFUSED on Linux — the socket
    inode survives close(). Non-Linux platforms are out of scope; tests that
    depend on ECONNREFUSED semantics should guard with skipif(sys.platform).
    """
    sock_path = tmp_path / "refused.sock"
    with bound_but_closed_socket(sock_path):
        yield sock_path
