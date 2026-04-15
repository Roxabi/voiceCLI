"""RED-phase tests for _probe_socket_daemon in voicecli.cli (issue #42).

These tests FAIL until _probe_socket_daemon is implemented.
They define the contract for the three return values:
  "absent"  — socket path does not exist
  "stale"   — path exists but no listener is accepting connections
  "live"    — path exists and a listener accepts the connection
"""

from __future__ import annotations

import socket
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import pytest

from voicecli.cli import _probe_socket_daemon


# ---------------------------------------------------------------------------
# Helper — minimal Unix domain socket listener
# ---------------------------------------------------------------------------


@contextmanager
def _spawn_unix_listener(path: Path) -> Generator[None, None, None]:
    """Bind a real AF_UNIX listener at *path*, accept one connection, then close.

    The listener runs in a daemon thread so it does not block the test.  The
    context manager yields once the socket is bound and listening so the caller
    can probe immediately.
    """
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(1)
    srv.settimeout(2.0)

    def _accept_one() -> None:
        try:
            conn, _ = srv.accept()
            conn.close()
        except OSError:
            pass
        finally:
            srv.close()

    t = threading.Thread(target=_accept_one, daemon=True)
    t.start()
    try:
        yield
    finally:
        srv.close()
        t.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProbeSocketDaemon:
    def test_probe_returns_absent_when_no_socket(self, tmp_path: Path) -> None:
        """Returns "absent" when the socket path does not exist at all."""
        # Arrange
        missing = tmp_path / "missing.sock"

        # Act
        result = _probe_socket_daemon(missing)

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

    def test_probe_returns_stale_on_real_econnrefused(self, tmp_path: Path) -> None:
        """Returns "stale" on genuine ECONNREFUSED from an AF_UNIX socket fs entry.

        Bind + listen on an AF_UNIX socket, then close() without unlink()ing the
        path. On Linux the fs entry remains (inode type = socket) but connect()
        returns ECONNREFUSED because no process is accepting. This is the real
        stale-daemon path that the sibling ENOTSOCK test can't exercise.
        """
        # Arrange — bind, listen, then close to leave a refusing socket path
        sock_path = tmp_path / "refused.sock"
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sock_path))
        srv.listen(1)
        srv.close()
        assert sock_path.exists(), "socket fs entry should persist after close()"

        # Act
        result = _probe_socket_daemon(sock_path)

        # Assert
        assert result == "stale"

    def test_probe_returns_live_when_listener_accepts(self, tmp_path: Path) -> None:
        """Returns "live" when a real AF_UNIX listener is bound and accepting."""
        # Arrange
        sock_path = tmp_path / "live.sock"

        with _spawn_unix_listener(sock_path):
            # Act
            result = _probe_socket_daemon(sock_path)

        # Assert
        assert result == "live"
