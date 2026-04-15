"""Tests for NatsAdapterBase heartbeat payload fields and loop leak guard (issue #49).

SC-4: heartbeat_payload includes connected: bool and uptime_s: float.
SC-5: _heartbeat_loop does not accumulate pending coroutines across iterations.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from unittest.mock import AsyncMock

from voicecli.nats.base import NatsAdapterBase


class _NoOpAdapter(NatsAdapterBase):
    """Minimal concrete subclass — handle() is never called in these tests."""

    async def handle(self, msg, payload):  # type: ignore[override]
        return None


def _make_adapter(**kwargs) -> _NoOpAdapter:
    defaults = dict(
        subject="test.subject",
        queue_group="test.group",
        heartbeat_subject="test.heartbeat",
        service="test",
        worker_id="w-1",
    )
    defaults.update(kwargs)
    return _NoOpAdapter(**defaults)


class TestHeartbeatPayloadFields:
    def test_heartbeat_payload_fields(self) -> None:
        """heartbeat_payload returns connected: bool and uptime_s: float >= elapsed."""
        # Arrange
        adapter = _make_adapter()
        adapter._started_at = time.monotonic() - 2.5

        # Stub _nc so is_connected is accessible
        adapter._nc = type("FakeNATS", (), {"is_connected": True})()

        # Act
        payload = adapter.heartbeat_payload()

        # Assert
        assert payload["connected"] is True
        assert isinstance(payload["uptime_s"], float)
        assert payload["uptime_s"] >= 2.5

    def test_heartbeat_payload_connected_false_when_nc_none(self) -> None:
        """connected is False when _nc is None (adapter not yet connected)."""
        # Arrange
        adapter = _make_adapter()
        adapter._nc = None

        # Act
        payload = adapter.heartbeat_payload()

        # Assert
        assert payload["connected"] is False

    def test_heartbeat_payload_connected_false_when_nc_disconnected(self) -> None:
        """connected reflects _nc.is_connected when _nc is present."""
        # Arrange
        adapter = _make_adapter()
        adapter._nc = type("FakeNATS", (), {"is_connected": False})()

        # Act
        payload = adapter.heartbeat_payload()

        # Assert
        assert payload["connected"] is False


class TestHeartbeatLoopNoCoroutineLeak:
    def test_heartbeat_loop_no_coroutine_leak(self) -> None:
        """_heartbeat_loop does not accumulate pending coroutines across iterations.

        The old code used asyncio.shield(stop.wait()) inside asyncio.wait_for() — each
        timeout left the inner stop.wait() coroutine running, so N iterations accumulated
        N live waiters. The fix uses asyncio.sleep(interval) instead.

        Probe: compare the total live-task count at two points ~20 iterations apart.
        The fix keeps exactly one task alive (the heartbeat loop itself) while a leaking
        implementation would monotonically grow the count by ~1 per iteration.
        """

        async def _run() -> None:
            # Arrange
            adapter = _make_adapter(heartbeat_interval=0.01)
            adapter._nats_publish = AsyncMock()  # type: ignore[method-assign]
            publish_calls_before = adapter._nats_publish.await_count

            stop = asyncio.Event()
            hb_task = asyncio.create_task(adapter._heartbeat_loop(stop))

            # Act — let the loop run for ~20 iterations
            await asyncio.sleep(0.2)
            snapshot_1 = len(asyncio.all_tasks())
            publish_calls_mid = adapter._nats_publish.await_count

            # Let it run ~20 more iterations
            await asyncio.sleep(0.2)
            snapshot_2 = len(asyncio.all_tasks())
            publish_calls_end = adapter._nats_publish.await_count

            # Cleanup must happen before assertions so a failure doesn't leak hb_task
            stop.set()
            hb_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb_task

            # Assert — total task count must not grow (leak would cause ~20 per window)
            assert snapshot_2 - snapshot_1 <= 1, (
                f"Coroutine leak detected: snapshot_1={snapshot_1}, snapshot_2={snapshot_2}. "
                "Each iteration of _heartbeat_loop is accumulating a pending waiter."
            )

            # Sanity — the loop must have actually iterated between snapshots; otherwise
            # a stalled loop would falsely satisfy the leak assertion
            assert publish_calls_mid > publish_calls_before, "loop never iterated before snapshot_1"
            assert publish_calls_end > publish_calls_mid, (
                "loop never iterated between snapshots — "
                "test is not exercising the per-iteration path"
            )

        asyncio.run(_run())
