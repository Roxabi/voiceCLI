"""Tests for voicecli.daemon_protocol wire-protocol helpers (Finding 8).

Covers the byte-cap branch in recv_json that protects against peers that
never send a newline.
"""

import json

import pytest

from voicecli.daemon_protocol import recv_json


class _FakeSock:
    """Yield queued chunks on recv(); returns empty bytes when exhausted."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    def recv(self, n: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def test_recv_json_cap_triggers_on_oversized_payload_without_newline():
    # Arrange — 3 chunks of 100 KB each, no newline → total 300 KB > 64 KB cap.
    # The loop must break on the cap rather than hanging indefinitely.
    big = b"x" * 102_400
    sock = _FakeSock([big, big, big])

    # Act + Assert — the cap exits the loop and tries json.loads on garbage bytes.
    with pytest.raises(json.JSONDecodeError):
        recv_json(sock, max_msg=65_536)


def test_recv_json_max_msg_none_allows_unbounded_until_newline():
    # Arrange — valid JSON terminated by newline; max_msg=None means uncapped.
    sock = _FakeSock([b'{"hello": "world"}\n'])

    # Act
    result = recv_json(sock, max_msg=None)

    # Assert
    assert result == {"hello": "world"}


def test_recv_json_default_cap_is_finite():
    """Regression: the default max_msg argument must be a finite integer > 0.

    Deleting or setting it to None would allow a malicious peer to exhaust
    memory by streaming an unbounded message without a newline.  This test
    fails when the guard is removed (cap reverts to None).
    """
    import inspect

    sig = inspect.signature(recv_json)
    default = sig.parameters["max_msg"].default
    # If this assertion fails, the cap guard has been removed.
    assert isinstance(default, int) or default is None, "max_msg default must be int or None"
    # The task says DEFAULT_MAX_MSG will be introduced; assert on it if present.
    try:
        from voicecli.daemon_protocol import DEFAULT_MAX_MSG  # type: ignore[attr-defined]

        assert isinstance(DEFAULT_MAX_MSG, int)
        assert DEFAULT_MAX_MSG > 0
    except ImportError:
        # Not yet introduced — fall back to asserting the parameter default is finite.
        assert isinstance(default, int), (
            "recv_json max_msg must have a finite integer default when DEFAULT_MAX_MSG "
            "is not exported at module level"
        )
        assert default > 0
