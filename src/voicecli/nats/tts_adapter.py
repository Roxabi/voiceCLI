"""TtsNatsAdapter — voicecli NATS satellite for TTS synthesis requests."""

from __future__ import annotations

import asyncio
import base64
import functools
import logging
import os
import re
import socket
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from voicecli.nats.base import NatsAdapterBase
from voicecli.nats.queue_groups import TTS_WORKERS
from voicecli.nats.reply import build_reply
from voicecli.nats.tempdir import cleanup, scoped_path

# voicecli.api is NOT imported at module level — deferred to keep startup fast
# and avoid pulling torch when only inspecting the adapter (e.g. for --help).

log = logging.getLogger(__name__)

DEFAULT_ENGINE = "qwen-fast"
SUBJECT = "lyra.voice.tts.request"
HEARTBEAT_SUBJECT = "lyra.voice.tts.heartbeat"


def _resolve_engine(cli_value: str | None = None) -> str:
    """Resolve TTS engine: CLI arg > VOICECLI_ENGINE > LYRA_TTS_ENGINE > voicecli.toml > DEFAULT_ENGINE."""
    if cli_value:
        return cli_value
    for env_var in ("VOICECLI_ENGINE", "LYRA_TTS_ENGINE"):
        v = os.environ.get(env_var)
        if v:
            return v
    try:
        from voicecli.config import load_config

        cfg = load_config()
        toml_engine = cfg.get("defaults", {}).get("engine")
        if toml_engine:
            return toml_engine
    except Exception:
        pass
    return DEFAULT_ENGINE


def _engine_available(engine: str) -> bool:
    from voicecli.engine import _get_registry

    return engine in _get_registry()


def _wav_duration_ms(path: Path) -> int:
    """Read WAV header to compute duration in milliseconds. Returns 0 on failure."""
    try:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate > 0:
                return int(frames / rate * 1000)
    except Exception:
        pass
    return 0


class TtsNatsAdapter(NatsAdapterBase):
    def __init__(
        self,
        *,
        default_engine: str,
        max_concurrent: int = 1,
        reject_when_full: bool = False,
        heartbeat_interval: float = 5.0,
        drain_timeout: float = 30.0,
    ) -> None:
        worker_id = f"tts-{socket.gethostname()}-{os.getpid()}-{int(time.time())}"
        super().__init__(
            subject=SUBJECT,
            queue_group=TTS_WORKERS,
            heartbeat_subject=HEARTBEAT_SUBJECT,
            service="tts_workers",
            worker_id=worker_id,
            heartbeat_interval=heartbeat_interval,
            drain_timeout=drain_timeout,
        )
        self.default_engine = default_engine
        self.max_concurrent = max_concurrent
        self.reject_when_full = reject_when_full
        self._sem = asyncio.Semaphore(max_concurrent)
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent)

    async def handle(self, msg: Any, payload: dict) -> None:  # type: ignore[override]
        request_id = payload.get("request_id", "")
        if not request_id:
            await self.reply(msg, build_reply(ok=False, request_id="", error="malformed_request"))
            return

        # Reject path-traversal or oversized request IDs at ingestion (Fix 2)
        if not re.match(r"^[A-Za-z0-9_-]{1,128}$", request_id):
            await self.reply(
                msg,
                build_reply(
                    ok=False,
                    request_id=request_id[:64] if request_id else "",
                    error="malformed_request",
                ),
            )
            return

        # Validate text field early to avoid KeyError being masked as synthesis_failed (Fix 8)
        text = payload.get("text")
        if not text or not isinstance(text, str):
            await self.reply(
                msg, build_reply(ok=False, request_id=request_id, error="malformed_request")
            )
            return

        engine = payload.get("engine") or self.default_engine
        if not _engine_available(engine):
            await self.reply(
                msg, build_reply(ok=False, request_id=request_id, error="engine_unavailable")
            )
            return

        if self.reject_when_full:
            # Non-blocking acquire: avoid the race in _sem.locked() (Fix 7)
            try:
                await asyncio.wait_for(self._sem.acquire(), timeout=0)
            except asyncio.TimeoutError:
                await self.reply(
                    msg, build_reply(ok=False, request_id=request_id, error="capacity_exceeded")
                )
                return
            try:
                await self._run_synthesis(msg, payload, request_id, text, engine)
            finally:
                self._sem.release()
        else:
            async with self._sem:
                await self._run_synthesis(msg, payload, request_id, text, engine)

    async def _run_synthesis(
        self, msg: Any, payload: dict, request_id: str, text: str, engine: str
    ) -> None:
        out_path = scoped_path(request_id, "wav")
        try:
            self.model_loaded = engine
            from voicecli import api

            optional_kwargs = {
                k: v
                for k, v in {
                    "language": payload.get("language"),
                    "voice": payload.get("voice"),
                    "speed": payload.get("speed"),
                    "exaggeration": payload.get("exaggeration"),
                    "cfg_weight": payload.get("cfg_weight"),
                }.items()
                if v is not None
            }
            fn = functools.partial(
                api.generate,
                text,
                engine=engine,
                output=out_path,
                **optional_kwargs,
            )
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, fn)
            audio_b64 = base64.b64encode(out_path.read_bytes()).decode("ascii")
            duration_ms = _wav_duration_ms(out_path)
            await self.reply(
                msg,
                build_reply(
                    ok=True,
                    request_id=request_id,
                    audio_b64=audio_b64,
                    mime_type="audio/wav",
                    duration_ms=duration_ms,
                ),
            )
        except Exception:
            log.exception("synthesis_failed", extra={"request_id": request_id})
            await self.reply(
                msg, build_reply(ok=False, request_id=request_id, error="synthesis_failed")
            )
        finally:
            cleanup(out_path)
