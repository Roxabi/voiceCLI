"""Shared test doubles and helpers for the NATS adapter test suite.

Kept lightweight on purpose — only primitives used by 2+ test modules live
here. Adapter-specific helpers (payload factories, stub engines) stay local
to the module that owns the adapter.

Contents:
  * ``FakeMsg`` — canonical NATS message stand-in (was duplicated 3×).
  * ``spawn_unix_listener`` — context manager that binds + accepts one conn.
  * ``bound_but_closed_socket`` — context manager that leaves a refusing
    socket fs entry (for probing the ECONNREFUSED branch).
  * ``assert_dispatch_outcome`` — three-way assertion for ``_dispatch`` tests.
"""

from __future__ import annotations

import json
import socket
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from roxabi_nats import NatsAdapterBase


class FakeMsg:
    """Minimal NATS message stand-in: carries ``.reply`` + ``.data`` and
    records every ``respond()`` call.

    Ctor accepts ``data`` for tests that drive ``_dispatch`` (which reads the
    payload off ``msg.data``); tests that call ``adapter.handle(msg, payload)``
    directly can leave ``data`` as an empty bytes literal.
    """

    def __init__(self, data: bytes = b"", reply_subject: str = "_INBOX.test") -> None:
        self.data = data
        self.reply = reply_subject
        self._published: list[bytes] = []

    async def respond(self, data: bytes) -> None:
        self._published.append(data)

    def last_reply(self) -> dict:
        assert self._published, "No reply published"
        return json.loads(self._published[-1])

    @property
    def responses(self) -> list[bytes]:
        return list(self._published)


class FakeNatsConn:
    """Mock NATS connection for testing SDK-based adapters.

    The SDK's reply() method publishes via _nc.publish(msg.reply, data).
    This mock records publishes and forwards them to the associated FakeMsg.
    """

    def __init__(self, msg: FakeMsg | None = None) -> None:
        self._msg = msg
        self._published: list[tuple[str, bytes]] = []
        self.is_connected = True
        self.is_closed = False

    def set_msg(self, msg: FakeMsg) -> None:
        """Associate a message for respond() forwarding."""
        self._msg = msg

    async def publish(self, subject: str, data: bytes) -> None:
        """Record publish and forward to associated message's respond()."""
        self._published.append((subject, data))
        if self._msg is not None:
            await self._msg.respond(data)


@contextmanager
def spawn_unix_listener(path: Path) -> Generator[None, None, None]:
    """Bind a real AF_UNIX listener at *path*, accept one connection, close.

    Yields once the socket is bound and listening. Runs the accept in a daemon
    thread so it never blocks the test.
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


@contextmanager
def bound_but_closed_socket(path: Path) -> Generator[None, None, None]:
    """Bind + listen + close an AF_UNIX socket, leaving the fs entry behind.

    On Linux the socket inode survives close() — connect() then returns
    ECONNREFUSED, modelling a crashed daemon whose socket file wasn't cleaned
    up. The fs entry is unlinked on exit.
    """
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(str(path))
        srv.listen(1)
    finally:
        srv.close()
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def assert_dispatch_outcome(
    adapter: "NatsAdapterBase",
    msg: FakeMsg,
    *,
    handled: bool,
    replied: bool,
    counter: int = 0,
) -> None:
    """Assert the three invariants a ``_dispatch`` test should always pin.

    Call after driving ``await adapter._dispatch(msg)``.

    Args:
        handled: expected presence of at least one ``handle()`` call — read
            from ``adapter.handle_calls`` (``_RecordingAdapter`` convention).
        replied: expected presence of at least one ``msg.respond()`` call.
        counter: expected value of ``adapter._active_requests`` after dispatch.
    """
    handle_calls = getattr(adapter, "handle_calls", None)
    if handle_calls is None:
        raise AssertionError(
            "assert_dispatch_outcome requires an adapter with a "
            "`handle_calls` list — use a Recording subclass."
        )
    actual_handled = len(handle_calls) >= 1
    actual_replied = len(msg.responses) >= 1
    assert actual_handled is handled, (
        f"handled: expected {handled}, got {actual_handled} ({len(handle_calls)} handle() calls)"
    )
    assert actual_replied is replied, (
        f"replied: expected {replied}, got {actual_replied} ({len(msg.responses)} responses)"
    )
    assert adapter._active_requests == counter, (
        f"counter: expected {counter}, got {adapter._active_requests}"
    )
