"""Shared fixtures for the NATS adapter test suite.

Exposes three socket-state fixtures and a re-export of the canonical FakeMsg.

Convention: every async test in this package uses **one** ``asyncio.run`` per
test function. Splitting a test across two ``asyncio.run`` calls means any
adapter-instance state set inside the first loop leaks into the second, and
any ``caplog`` capture window is split across two distinct loops. Use a
single coroutine that awaits the sequential operations instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest

from _fakes import FakeMsg, bound_but_closed_socket, spawn_unix_listener

__all__ = [
    "FakeMsg",
    "absent_socket_path",
    "live_socket_path",
    "stale_socket_path",
]


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
