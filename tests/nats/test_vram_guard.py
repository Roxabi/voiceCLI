"""Tests for _probe_socket_daemon in voicecli.cli (issues #42, #48).

Defines the contract for the three return values of _probe_socket_daemon:
  "absent"  — socket path does not exist
  "stale"   — path exists but no listener is accepting connections
  "live"    — path exists and a listener accepts the connection

Socket-state helpers live in ``tests/nats/_fakes.py`` and are exposed as
``absent_socket_path`` / ``stale_socket_path`` / ``live_socket_path`` fixtures
in ``tests/nats/conftest.py``.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from voicecli.cli import _probe_socket_daemon


class TestProbeSocketDaemon:
    def test_probe_returns_absent_when_no_socket(self, absent_socket_path: Path) -> None:
        """Returns "absent" when the socket path does not exist at all."""
        # Act
        result = _probe_socket_daemon(absent_socket_path)

        # Assert
        assert result == "absent"

    def test_probe_returns_stale_when_enotsock(self, tmp_path: Path) -> None:
        """Returns "stale" when the path is a regular file (ENOTSOCK on connect).

        A plain regular file produces ENOTSOCK — not ECONNREFUSED — but represents
        the same semantic state: a path is present yet no live daemon is behind it.
        The implementation must treat any connection failure as "stale".
        """
        # Arrange
        stale = tmp_path / "stale.sock"
        stale.touch()  # regular file, not a socket — connect() gives ENOTSOCK

        # Act
        result = _probe_socket_daemon(stale)

        # Assert
        assert result == "stale"

    @pytest.mark.skipif(
        sys.platform != "linux",
        reason="ECONNREFUSED after bind+close without unlink is a Linux-specific contract",
    )
    def test_probe_returns_stale_on_real_econnrefused(self, stale_socket_path: Path) -> None:
        """Returns "stale" on genuine ECONNREFUSED from an AF_UNIX socket fs entry.

        The ``stale_socket_path`` fixture binds + listens + closes an AF_UNIX
        socket without unlinking it. On Linux the fs entry survives close()
        (socket inode persists) but connect() returns ECONNREFUSED because no
        process is accepting. Fixture handles cleanup.
        """
        # Sanity — the fixture should have left the inode as a socket
        assert stat.S_ISSOCK(stale_socket_path.stat().st_mode), (
            "stale_socket_path should be a socket inode"
        )

        # Act
        result = _probe_socket_daemon(stale_socket_path)

        # Assert
        assert result == "stale"

    def test_probe_returns_live_when_listener_accepts(self, live_socket_path: Path) -> None:
        """Returns "live" when a real AF_UNIX listener is bound and accepting."""
        # Act
        result = _probe_socket_daemon(live_socket_path)

        # Assert
        assert result == "live"
