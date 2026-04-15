"""Direct tests for NatsAdapterBase._dispatch (issue #48).

These exercise the _dispatch path directly — malformed-JSON recovery,
contract_version defensive read (warn once then proceed), the active-requests
counter balance across success / error / None-return paths, and the contract
that ``handle() -> None`` skips the reply step.

Adapter-state invariants are pinned via ``assert_dispatch_outcome`` (see
``tests/nats/_fakes.py``): handled?, replied?, counter balance.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import pytest
from _fakes import FakeMsg, assert_dispatch_outcome

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


class _NoReplyAdapter(NatsAdapterBase):
    """handle() records the call and returns None — base must NOT reply."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.handle_calls: list[dict] = []

    async def handle(self, msg, payload):  # type: ignore[override]
        self.handle_calls.append(payload)
        return None


class _RaisingAdapter(NatsAdapterBase):
    """handle() records the call then raises — exercises the counter balance."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.handle_calls: list[dict] = []

    async def handle(self, msg, payload):  # type: ignore[override]
        self.handle_calls.append(payload)
        raise RuntimeError("boom")


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


def _encode(payload: dict) -> bytes:
    return json.dumps(payload).encode()


def _run(coro_factory: Callable[[], Awaitable[None]]) -> None:
    """Single-entry asyncio.run wrapper — enforces one loop per test."""
    asyncio.run(coro_factory())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Malformed JSON / UTF-8
# ---------------------------------------------------------------------------


class TestDispatchMalformedJson:
    def test_dispatch_malformed_json_returns_malformed_request_error(self) -> None:
        """_dispatch replies malformed_request and skips handle() on invalid JSON."""
        # Arrange
        adapter = _make_adapter()
        msg = FakeMsg(data=b"not-json{")

        # Act
        _run(lambda: adapter._dispatch(msg))

        # Assert
        assert_dispatch_outcome(adapter, msg, handled=False, replied=True, counter=0)
        assert msg.last_reply()["ok"] is False
        assert msg.last_reply()["error"] == "malformed_request"

    def test_dispatch_malformed_utf8_returns_malformed_request_error(self) -> None:
        """Invalid UTF-8 bytes are caught by the same branch as bad JSON."""
        # Arrange — lone continuation bytes are invalid UTF-8
        adapter = _make_adapter()
        msg = FakeMsg(data=b"\xff\xfe\x00")

        # Act
        _run(lambda: adapter._dispatch(msg))

        # Assert
        assert_dispatch_outcome(adapter, msg, handled=False, replied=True, counter=0)
        assert msg.last_reply()["ok"] is False
        assert msg.last_reply()["error"] == "malformed_request"


# ---------------------------------------------------------------------------
# contract_version warn-once
# ---------------------------------------------------------------------------


class TestDispatchContractVersion:
    def test_dispatch_contract_version_999_warns_once_and_proceeds(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Unexpected contract_version logs WARN exactly once then proceeds.

        Driven through a single asyncio.run so both dispatches share one event
        loop, one caplog window, and one adapter instance (the warn-once flag
        is sticky on the adapter — that's what we're pinning).

        Mirrors lyra#688 T7 but via the real _dispatch path.
        """
        # Arrange
        adapter = _make_adapter()
        payload = {"contract_version": "999", "request_id": "req-cv"}
        msg1 = FakeMsg(data=_encode(payload))
        msg2 = FakeMsg(data=_encode(payload))

        async def _sequence() -> None:
            await adapter._dispatch(msg1)
            await adapter._dispatch(msg2)

        # Act
        with caplog.at_level(logging.WARNING, logger="voicecli.nats.base"):
            _run(_sequence)

        # Assert — handle() ran twice despite the mismatch, replies sent
        assert len(adapter.handle_calls) == 2  # type: ignore[attr-defined]
        assert_dispatch_outcome(adapter, msg1, handled=True, replied=True, counter=0)

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

    def test_dispatch_matching_contract_version_runs_handle_and_does_not_warn(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Matching contract_version runs handle() and emits no warning."""
        # Arrange
        adapter = _make_adapter()
        payload = {"contract_version": CONTRACT_VERSION, "request_id": "req-ok"}
        msg = FakeMsg(data=_encode(payload))

        # Act
        with caplog.at_level(logging.WARNING, logger="voicecli.nats.base"):
            _run(lambda: adapter._dispatch(msg))

        # Assert — handle() ran, reply sent, no contract warning emitted
        assert_dispatch_outcome(adapter, msg, handled=True, replied=True, counter=0)
        contract_warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "contract_version" in r.getMessage()
        ]
        assert contract_warnings == []


# ---------------------------------------------------------------------------
# Counter balance — decoupled from exception propagation
# ---------------------------------------------------------------------------


class TestDispatchActiveRequestsCounter:
    """The counter contract is: every dispatch that increments must decrement.

    Decoupled from exception propagation: a future change to _dispatch that
    swallows handle() exceptions would still be required to balance the
    counter, and these tests would still pass. The separate
    TestDispatchExceptionPropagation class pins the re-raise contract so the
    two concerns can evolve independently.
    """

    def test_counter_balanced_on_success(self) -> None:
        """Counter returns to zero after a normal handle()."""
        adapter = _make_adapter()
        payload = {"contract_version": CONTRACT_VERSION, "request_id": "req-ok"}
        msg = FakeMsg(data=_encode(payload))

        _run(lambda: adapter._dispatch(msg))

        assert adapter._active_requests == 0

    def test_counter_balanced_on_handle_returning_none(self) -> None:
        """Counter returns to zero even when handle() returns None (no reply)."""
        adapter = _make_adapter(cls=_NoReplyAdapter)
        payload = {"contract_version": CONTRACT_VERSION, "request_id": "req-none"}
        msg = FakeMsg(data=_encode(payload))

        _run(lambda: adapter._dispatch(msg))

        # handle() ran but no reply was sent; counter still balances
        assert_dispatch_outcome(adapter, msg, handled=True, replied=False, counter=0)

    def test_counter_balanced_on_handle_error(self) -> None:
        """Counter returns to zero even when handle() raises.

        Asserts the counter directly — does not rely on pytest.raises catching
        the propagated exception. The coroutine itself wraps the _dispatch
        call in try/finally so the assertion is independent of whether
        _dispatch re-raises or swallows.
        """
        adapter = _make_adapter(cls=_RaisingAdapter)
        payload = {"contract_version": CONTRACT_VERSION, "request_id": "req-raise"}
        msg = FakeMsg(data=_encode(payload))

        async def _dispatch_swallow() -> None:
            try:
                await adapter._dispatch(msg)
            except RuntimeError:
                pass

        _run(_dispatch_swallow)

        assert adapter._active_requests == 0
        # handle() was called before raising; no reply was sent
        assert len(adapter.handle_calls) == 1  # type: ignore[attr-defined]
        assert msg.responses == []

    def test_counter_balanced_on_malformed_json(self) -> None:
        """Counter never increments when the payload fails to parse."""
        adapter = _make_adapter()
        msg = FakeMsg(data=b"nope{")

        _run(lambda: adapter._dispatch(msg))

        # Never entered the handle() guarded region — counter was never
        # incremented so "balanced" is trivially satisfied
        assert adapter._active_requests == 0
        assert adapter.handle_calls == []  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Exception propagation contract — separate concern from counter balance
# ---------------------------------------------------------------------------


class TestDispatchExceptionPropagation:
    def test_dispatch_propagates_handle_exceptions_to_caller(self) -> None:
        """_dispatch currently re-raises handle() exceptions.

        Production NATS callbacks catch these upstream. Pinning the contract
        separately from the counter test so a future "swallow exceptions
        inside _dispatch" change is flagged explicitly rather than as a
        spurious counter regression.
        """
        adapter = _make_adapter(cls=_RaisingAdapter)
        payload = {"contract_version": CONTRACT_VERSION, "request_id": "req-raise"}
        msg = FakeMsg(data=_encode(payload))

        with pytest.raises(RuntimeError, match="boom"):
            _run(lambda: adapter._dispatch(msg))


# ---------------------------------------------------------------------------
# handle() → None contract (no reply sent by base)
# ---------------------------------------------------------------------------


class TestDispatchNoneReply:
    def test_dispatch_skips_reply_when_handle_returns_none(self) -> None:
        """Base must NOT call reply() when handle() returns None.

        Documents the documented contract: subclasses that respond directly
        via msg.respond() return None to tell the base to stay out of the way.
        """
        adapter = _make_adapter(cls=_NoReplyAdapter)
        payload = {"contract_version": CONTRACT_VERSION, "request_id": "req-none"}
        msg = FakeMsg(data=_encode(payload))

        _run(lambda: adapter._dispatch(msg))

        assert_dispatch_outcome(adapter, msg, handled=True, replied=False, counter=0)
