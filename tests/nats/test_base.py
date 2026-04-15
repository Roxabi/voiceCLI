"""Direct tests for NatsAdapterBase._dispatch (issue #48).

These exercise the _dispatch path directly — malformed-JSON recovery,
contract_version defensive read (warn once then proceed), and the
active-requests counter balance when handle() raises.

The existing adapter test suites call adapter.handle() directly, which
bypasses _dispatch; these tests close that gap.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from voicecli.nats.base import NatsAdapterBase
from voicecli.nats.reply import CONTRACT_VERSION


class _RecordingAdapter(NatsAdapterBase):
    """Records handle() calls and replies a fixed ok payload."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.handle_calls: list[dict] = []

    async def handle(self, msg, payload):  # type: ignore[override]
        self.handle_calls.append(payload)
        return {"ok": True, "request_id": payload.get("request_id", "")}


class _RaisingAdapter(NatsAdapterBase):
    """handle() always raises — exercises the counter-balance finally."""

    async def handle(self, msg, payload):  # type: ignore[override]
        raise RuntimeError("boom")


class _MockMsg:
    """Minimal NATS message stand-in — captures responses."""

    def __init__(self, data: bytes, reply_subject: str = "_INBOX.test") -> None:
        self.data = data
        self.reply = reply_subject
        self.responses: list[bytes] = []

    async def respond(self, data: bytes) -> None:
        self.responses.append(data)

    def last_reply(self) -> dict:
        assert self.responses, "No reply captured"
        return json.loads(self.responses[-1])


def _make_adapter(cls: type[NatsAdapterBase] = _RecordingAdapter, **kwargs) -> NatsAdapterBase:
    defaults = dict(
        subject="test.subject",
        queue_group="test.group",
        heartbeat_subject="test.heartbeat",
        service="test",
        worker_id="w-1",
    )
    defaults.update(kwargs)
    return cls(**defaults)


class TestDispatchMalformedJson:
    def test_dispatch_malformed_json_returns_malformed_request_error(self) -> None:
        """_dispatch replies malformed_request and skips handle() on invalid JSON."""
        # Arrange
        adapter = _make_adapter()
        msg = _MockMsg(data=b"not-json{")

        # Act
        asyncio.run(adapter._dispatch(msg))

        # Assert — error reply sent, handle() never invoked
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"
        assert adapter.handle_calls == []  # type: ignore[attr-defined]

    def test_dispatch_malformed_utf8_returns_malformed_request_error(self) -> None:
        """Invalid UTF-8 bytes are caught by the same branch as bad JSON."""
        # Arrange — lone continuation byte is invalid UTF-8
        adapter = _make_adapter()
        msg = _MockMsg(data=b"\xff\xfe\x00")

        # Act
        asyncio.run(adapter._dispatch(msg))

        # Assert
        reply = msg.last_reply()
        assert reply["ok"] is False
        assert reply["error"] == "malformed_request"
        assert adapter.handle_calls == []  # type: ignore[attr-defined]


class TestDispatchContractVersion:
    def test_dispatch_contract_version_999_warns_once_and_proceeds(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Unexpected contract_version logs WARN exactly once then proceeds.

        Mirrors lyra#688 T7 but via the real _dispatch path (previous coverage
        asserted the behavior without going through _dispatch).
        """
        # Arrange
        adapter = _make_adapter()
        payload = {"contract_version": "999", "request_id": "req-cv"}
        data = json.dumps(payload).encode()

        # Act — dispatch twice with the same mismatched version
        with caplog.at_level(logging.WARNING, logger="voicecli.nats.base"):
            asyncio.run(adapter._dispatch(_MockMsg(data=data)))
            asyncio.run(adapter._dispatch(_MockMsg(data=data)))

        # Assert — handle() ran twice despite the mismatch
        assert len(adapter.handle_calls) == 2  # type: ignore[attr-defined]

        # Assert — exactly one WARN about contract_version
        contract_warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "contract_version" in r.getMessage()
        ]
        assert len(contract_warnings) == 1, (
            f"expected 1 contract_version warning, got {len(contract_warnings)}"
        )
        assert "999" in contract_warnings[0].getMessage()

    def test_dispatch_matching_contract_version_does_not_warn(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Matching contract_version emits no warning."""
        # Arrange
        adapter = _make_adapter()
        payload = {"contract_version": CONTRACT_VERSION, "request_id": "req-ok"}
        data = json.dumps(payload).encode()

        # Act
        with caplog.at_level(logging.WARNING, logger="voicecli.nats.base"):
            asyncio.run(adapter._dispatch(_MockMsg(data=data)))

        # Assert
        contract_warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "contract_version" in r.getMessage()
        ]
        assert contract_warnings == []


class TestDispatchActiveRequestsCounter:
    def test_dispatch_active_requests_counter_balanced_on_error(self) -> None:
        """Counter must return to zero even when handle() raises."""
        # Arrange
        adapter = _make_adapter(cls=_RaisingAdapter)
        payload = {"contract_version": CONTRACT_VERSION, "request_id": "req-raise"}
        data = json.dumps(payload).encode()

        # Sanity
        assert adapter._active_requests == 0

        # Act — _dispatch re-raises handle()'s exception; we catch it
        with pytest.raises(RuntimeError, match="boom"):
            asyncio.run(adapter._dispatch(_MockMsg(data=data)))

        # Assert — finally block decremented the counter
        assert adapter._active_requests == 0

    def test_dispatch_active_requests_counter_balanced_on_success(self) -> None:
        """Counter returns to zero on normal completion."""
        # Arrange
        adapter = _make_adapter()
        payload = {"contract_version": CONTRACT_VERSION, "request_id": "req-ok"}
        data = json.dumps(payload).encode()

        # Act
        asyncio.run(adapter._dispatch(_MockMsg(data=data)))

        # Assert
        assert adapter._active_requests == 0
