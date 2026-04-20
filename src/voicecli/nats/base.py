"""NatsAdapterBase — lifecycle host for voiceCLI NATS request-reply adapters."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
import socket
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS  # type: ignore[import-untyped]
    from nats.aio.msg import Msg  # type: ignore[import-untyped]

from voicecli.nats.connect import nats_connect
from voicecli.nats.nvml import read_vram
from voicecli.nats.reply import CONTRACT_VERSION, build_reply, encode_reply

log = logging.getLogger(__name__)


class DrainTimeoutError(RuntimeError):
    """Raised when active requests do not drain within drain_timeout seconds."""


class NatsAdapterBase:
    def __init__(
        self,
        *,
        subject: str,
        queue_group: str,
        heartbeat_subject: str,
        service: str,
        worker_id: str,
        heartbeat_interval: float = 5.0,
        drain_timeout: float = 30.0,
    ) -> None:
        self.subject = subject
        self.queue_group = queue_group
        self.heartbeat_subject = heartbeat_subject
        self.service = service
        self.worker_id = worker_id
        self.heartbeat_interval = heartbeat_interval
        self.drain_timeout = drain_timeout
        self._nc: NATS | None = None
        self._active_requests: int = 0
        self.model_loaded: str | None = None
        self._contract_version_warned: bool = False
        self._started_at: float = time.monotonic()

    async def run(
        self,
        nats_url: str,
        stop: asyncio.Event | None = None,
        nkey_seed_path: Path | None = None,
    ) -> None:
        if nkey_seed_path is not None:
            log.info("authenticated to NATS with nkey seed at %s", nkey_seed_path)
        else:
            log.info("anonymous NATS connection; set NATS_NKEY_SEED_PATH to enable nkey auth")
        nc = await nats_connect(nats_url, nkey_seed_path=nkey_seed_path)
        self._nc = nc

        # voicecli satellites do NOT probe lyra hub readiness — the satellite
        # owns its own lifecycle and trusts NATS reconnect to handle hub flakes.

        sub = await nc.subscribe(self.subject, queue=self.queue_group, cb=self._dispatch)
        # Per-worker subject for targeted delivery from the hub
        # (hub publishes to ``<subject>.<worker_id>`` for least-loaded routing)
        worker_sub = await nc.subscribe(f"{self.subject}.{self.worker_id}", cb=self._dispatch)
        hb_sub = await nc.subscribe(self.heartbeat_subject, cb=self._noop_cb)

        if stop is None:
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            try:
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                # Windows fallback
                def _make_handler(ev: asyncio.Event):
                    def _handler(signum, frame):  # noqa: ARG001
                        ev.set()

                    return _handler

                for sig in (signal.SIGTERM, signal.SIGINT):
                    signal.signal(sig, _make_handler(stop))

        hb_task = asyncio.create_task(self._heartbeat_loop(stop))

        await stop.wait()

        # Unsubscribe all subjects
        with contextlib.suppress(Exception):
            await sub.unsubscribe()
        with contextlib.suppress(Exception):
            await worker_sub.unsubscribe()
        with contextlib.suppress(Exception):
            await hb_sub.unsubscribe()

        # Wait for active requests to drain
        deadline = time.monotonic() + self.drain_timeout
        timed_out = False
        while self._active_requests > 0:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            await asyncio.sleep(0.05)

        # Final heartbeat
        try:
            final = self.heartbeat_payload()
            final["model_loaded"] = None
            final["active_requests"] = 0
            if nc.is_connected:
                await self._nats_publish(self.heartbeat_subject, json.dumps(final).encode())
        except Exception:
            log.warning("base: final heartbeat publish failed", exc_info=True)

        hb_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb_task

        with contextlib.suppress(Exception):
            await nc.drain()
        with contextlib.suppress(Exception):
            await nc.close()

        if timed_out:
            raise DrainTimeoutError(f"Active requests did not drain within {self.drain_timeout}s")

    async def _noop_cb(self, msg: Msg) -> None:
        pass

    async def _nats_publish(self, subject: str, data: bytes) -> None:
        """Indirection for tests to intercept all outgoing publishes."""
        if self._nc is not None:
            await self._nc.publish(subject, data)

    async def handle(self, msg: Msg, payload: dict) -> dict | None:
        """Process a dispatch payload and return an optional reply dict.

        Contract for subclasses:
          * Return a dict → base calls ``self.reply(msg, dict)`` with the
            standard encoder.
          * Return ``None`` → the subclass is responsible for replying via
            ``msg.respond()`` directly, or for deliberately not replying
            (e.g. fire-and-forget variants). ``_dispatch`` skips the reply
            step in this case.

        The active-requests counter is balanced by ``_dispatch`` regardless
        of the return value or any exception raised here.
        """
        raise NotImplementedError

    async def reply(self, msg: Msg, payload: dict) -> None:
        """Encode payload and respond to msg."""
        data = encode_reply(payload)
        await msg.respond(data)

    @contextlib.asynccontextmanager
    async def _track_active_request(self):
        """Increment the active-requests counter for the scope of a dispatch.

        Balancing the counter in a reusable context manager keeps the
        invariant ("every started request is eventually decremented") in one
        place, and makes tests able to assert the balance directly without
        relying on whether ``_dispatch`` swallows or re-raises exceptions
        from ``handle()``.
        """
        self._active_requests += 1
        try:
            yield
        finally:
            self._active_requests -= 1

    def heartbeat_payload(self) -> dict:
        vram_used, vram_total = read_vram()
        return {
            "contract_version": CONTRACT_VERSION,
            "worker_id": self.worker_id,
            "service": self.service,
            "host": socket.gethostname(),
            "subject": self.subject,
            "queue_group": self.queue_group,
            "ts": time.time(),
            "model_loaded": self.model_loaded,
            "vram_used_mb": vram_used,
            "vram_total_mb": vram_total,
            "active_requests": self._active_requests,
            "connected": self._nc.is_connected if self._nc is not None else False,
            "uptime_s": time.monotonic() - self._started_at,
        }

    async def _heartbeat_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                payload = self.heartbeat_payload()
                await self._nats_publish(
                    self.heartbeat_subject,
                    json.dumps(payload).encode(),
                )
            except Exception:
                log.warning("base: heartbeat publish failed", exc_info=True)
            try:
                await asyncio.sleep(self.heartbeat_interval)
            except asyncio.CancelledError:
                break

    async def _dispatch(self, msg: Msg) -> None:
        try:
            payload = json.loads(msg.data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.error("base: malformed JSON on %s", self.subject)
            error_reply = build_reply(ok=False, request_id="", error="malformed_request")
            with contextlib.suppress(Exception):
                await msg.respond(encode_reply(error_reply))
            return

        if payload.get("contract_version") != CONTRACT_VERSION:
            if not self._contract_version_warned:
                self._contract_version_warned = True
                log.warning(
                    "base: unexpected contract_version %r on %s (expected %r) — continuing",
                    payload.get("contract_version"),
                    self.subject,
                    CONTRACT_VERSION,
                )

        async with self._track_active_request():
            result = await self.handle(msg, payload)
            if result is not None:
                await self.reply(msg, result)
