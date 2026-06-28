"""LifecycleMixin — NATS lifecycle control plane for voice NATS adapters."""

from __future__ import annotations

import asyncio
import logging
import socket
from datetime import datetime, timezone

from pydantic import ValidationError
from roxabi_contracts.errors import WorkerError
from roxabi_contracts.voice import VoiceLifecycleRequest, VoiceLifecycleResponse

log = logging.getLogger(__name__)


class LifecycleMixin:
    """Mixin adding NATS lifecycle handlers to TtsNatsAdapter / SttNatsAdapter."""

    def __init_lifecycle__(self) -> None:
        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()

    def _lifecycle_subjects(self) -> tuple[str, ...]:
        raise NotImplementedError

    def _extra_subjects(self) -> list[str]:
        return [*self._lifecycle_subjects(), *super()._extra_subjects()]  # type: ignore[misc]

    async def handle(self, msg, payload: dict) -> None:  # type: ignore[override]
        if msg.subject in self._lifecycle_subjects():
            await self.handle_lifecycle(msg, payload)
            return
        await super().handle(msg, payload)  # type: ignore[misc]

    async def handle_lifecycle(self, msg, payload: dict) -> None:
        try:
            req = VoiceLifecycleRequest.model_validate(payload)
        except ValidationError:
            log.exception("lifecycle: invalid payload on %s", msg.subject)
            return
        if req.host not in (None, "*", socket.gethostname()):
            return
        await self._dispatch_lifecycle_op(req.op, msg, req)

    async def _dispatch_lifecycle_op(self, op: str, msg, req: VoiceLifecycleRequest) -> None:
        handler = {
            "list": self._do_list,
            "status": self._do_status,
        }.get(op)
        if handler is None:
            log.warning("lifecycle: unknown op=%r — ignoring", op)
            return
        async with self._lifecycle_lock:
            await handler(msg, req)

    async def _reply_ok(self, msg, req: VoiceLifecycleRequest, *, data: dict | None = None) -> None:
        resp = VoiceLifecycleResponse(
            contract_version=req.contract_version,
            trace_id=req.trace_id,
            issued_at=datetime.now(timezone.utc),
            request_id=req.request_id,
            ok=True,
            host=socket.gethostname(),
            data=data,
        )
        if msg.reply and self._nc:  # type: ignore[attr-defined]
            await self._nc.publish(msg.reply, resp.model_dump_json(exclude_none=True).encode())  # type: ignore[attr-defined]

    async def _reply_err(
        self,
        msg,
        req: VoiceLifecycleRequest,
        code: str,
        message: str,
        *,
        retryable: bool = True,
    ) -> None:
        resp = VoiceLifecycleResponse(
            contract_version=req.contract_version,
            trace_id=req.trace_id,
            issued_at=datetime.now(timezone.utc),
            request_id=req.request_id,
            ok=False,
            host=socket.gethostname(),
            worker_error=WorkerError(code=code, message=message, retryable=retryable),
        )
        if msg.reply and self._nc:  # type: ignore[attr-defined]
            await self._nc.publish(msg.reply, resp.model_dump_json(exclude_none=True).encode())  # type: ignore[attr-defined]

    async def _do_list(self, msg, req: VoiceLifecycleRequest) -> None:
        raise NotImplementedError

    async def _do_status(self, msg, req: VoiceLifecycleRequest) -> None:
        raise NotImplementedError
