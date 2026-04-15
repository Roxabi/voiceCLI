"""TtsNatsAdapter — voicecli NATS satellite for TTS synthesis requests."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import re
import struct
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from roxabi_nats import NatsAdapterBase
from roxabi_nats._validate import validate_nats_token
from voicecli.nats.queue_groups import TTS_WORKERS
from voicecli.nats.reply import build_reply, encode_reply
from voicecli.nats.tempdir import cleanup, scoped_path

# voicecli.api is NOT imported at module level — deferred to keep startup fast
# and avoid pulling torch when only inspecting the adapter (e.g. for --help).

log = logging.getLogger(__name__)

SUBJECT = "lyra.voice.tts.request"
HEARTBEAT_SUBJECT = "lyra.voice.tts.heartbeat"


def _engine_available(engine: str) -> bool:
    from voicecli.engine import _get_registry

    return engine in _get_registry()


def _collect_chunked_output(out_path: Path) -> list[Path]:
    """Return sorted chunk paths if a .done marker exists, else empty list.

    When api.generate() runs in chunked mode it writes:
        {stem}_001.wav, {stem}_002.wav, … {stem}_NNN.wav
        {stem}.done  (sentinel written after all chunks)

    The adapter expects a single file at *out_path* ({stem}.wav). If the
    engine wrote chunks instead, this function returns them in order so the
    caller can concatenate them.
    """
    done_path = out_path.with_suffix(".done")
    if not done_path.exists():
        return []
    stem = out_path.stem
    parent = out_path.parent
    chunks = sorted(parent.glob(f"{stem}_*.wav"))
    return chunks


def _concat_wav_chunks(chunks: list[Path], out_path: Path) -> None:
    """Concatenate WAV chunk files into *out_path* using the stdlib wave module.

    All chunks must share the same format (channels, sample width, frame rate).
    Raises ValueError if the chunk list is empty or format is inconsistent.
    """
    if not chunks:
        raise ValueError("concat_wav_chunks: chunk list is empty")

    with wave.open(str(chunks[0]), "rb") as first:
        params = first.getparams()

    with wave.open(str(out_path), "wb") as out_wav:
        out_wav.setparams(params)
        for chunk_path in chunks:
            with wave.open(str(chunk_path), "rb") as chunk_wav:
                if (
                    chunk_wav.getnchannels() != params.nchannels
                    or chunk_wav.getsampwidth() != params.sampwidth
                    or chunk_wav.getframerate() != params.framerate
                ):
                    raise ValueError(
                        f"chunk format mismatch in {chunk_path}: "
                        f"expected {params.nchannels}ch/{params.sampwidth}sw/{params.framerate}Hz"
                    )
                out_wav.writeframes(chunk_wav.readframes(chunk_wav.getnframes()))


def _cleanup_chunks(out_path: Path, chunks: list[Path]) -> None:
    """Remove chunk files and the .done sentinel. Idempotent."""
    done_path = out_path.with_suffix(".done")
    for p in [*chunks, done_path]:
        with contextlib.suppress(FileNotFoundError):
            p.unlink()


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


def _wav_waveform_b64(path: Path, num_samples: int = 256) -> str | None:
    """Compute a 256-byte amplitude waveform from a WAV file.

    Mirrors lyra's `_wav_waveform_b64` so Discord voice-message waveforms
    can be rendered without a second decoding pass hub-side.
    Returns None on any error (field is optional in ADR-044).
    """
    try:
        with wave.open(str(path), "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)

        if sampwidth == 1:
            samples = [raw[i] - 128 for i in range(0, len(raw), n_channels)]
            max_val = 128
        elif sampwidth == 2:
            samples = [
                struct.unpack_from("<h", raw, i)[0] for i in range(0, len(raw) - 1, 2 * n_channels)
            ]
            max_val = 32768
        else:
            return None

        if not samples:
            return None

        chunk = max(1, len(samples) // num_samples)
        waveform = bytearray()
        for i in range(num_samples):
            sl = samples[i * chunk : i * chunk + chunk]
            amp = sum(abs(x) for x in sl) // len(sl) if sl else 0
            waveform.append(min(255, int(amp * 255 / max_val)))
        return base64.b64encode(bytes(waveform)).decode("ascii")
    except Exception:
        log.warning("waveform_b64 computation failed", exc_info=True)
        return None


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
        super().__init__(
            SUBJECT,
            TTS_WORKERS,
            envelope_name="tts",
            schema_version=1,
            drain_timeout=drain_timeout,
            heartbeat_subject=HEARTBEAT_SUBJECT,
            heartbeat_interval=heartbeat_interval,
        )
        self.default_engine = default_engine
        self.max_concurrent = max_concurrent
        self.reject_when_full = reject_when_full
        self.model_loaded: str | None = None
        self._sem = asyncio.Semaphore(max_concurrent)
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent)

    def heartbeat_payload(self) -> dict:
        payload = super().heartbeat_payload()
        payload["model_loaded"] = self.model_loaded
        payload["active_requests"] = self.max_concurrent - self._sem._value
        return payload

    def _extra_subjects(self) -> list[str]:
        return [f"{self.subject}.{self._worker_id}"]

    async def handle(self, msg: Any, payload: dict) -> None:  # type: ignore[override]
        request_id = payload.get("request_id", "")
        if not request_id:
            await self.reply(
                msg, encode_reply(build_reply(ok=False, request_id="", error="malformed_request"))
            )
            return

        # Reject path-traversal or oversized request IDs at ingestion (Fix 2)
        if not re.match(r"^[A-Za-z0-9_-]{1,128}$", request_id):
            await self.reply(
                msg,
                encode_reply(
                    build_reply(
                        ok=False,
                        request_id=request_id[:64] if request_id else "",
                        error="malformed_request",
                    )
                ),
            )
            return

        # Validate text field early to avoid KeyError being masked as synthesis_failed (Fix 8)
        text = payload.get("text")
        if not text or not isinstance(text, str):
            await self.reply(
                msg,
                encode_reply(
                    build_reply(ok=False, request_id=request_id, error="malformed_request")
                ),
            )
            return

        engine = payload.get("engine") or self.default_engine
        try:
            validate_nats_token(engine, kind="engine")
        except ValueError:
            await self.reply(
                msg,
                encode_reply(
                    build_reply(ok=False, request_id=request_id, error="malformed_request")
                ),
            )
            return
        if not _engine_available(engine):
            await self.reply(
                msg,
                encode_reply(
                    build_reply(ok=False, request_id=request_id, error="engine_unavailable")
                ),
            )
            return

        if self.reject_when_full:
            # Non-blocking acquire: avoid the race in _sem.locked() (Fix 7)
            try:
                await asyncio.wait_for(self._sem.acquire(), timeout=0)
            except asyncio.TimeoutError:
                await self.reply(
                    msg,
                    encode_reply(
                        build_reply(ok=False, request_id=request_id, error="capacity_exceeded")
                    ),
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

            # Engine-agnostic kwargs forwarded through api.generate **kwargs
            # (translate.py will strip fields the target engine can't consume).
            optional_kwargs = {
                k: v
                for k, v in {
                    "language": payload.get("language"),
                    "voice": payload.get("voice"),
                    "speed": payload.get("speed"),
                    "exaggeration": payload.get("exaggeration"),
                    "cfg_weight": payload.get("cfg_weight"),
                    "accent": payload.get("accent"),
                    "personality": payload.get("personality"),
                    "emotion": payload.get("emotion"),
                }.items()
                if v is not None
            }

            # Named parameters of api.generate — must be passed explicitly, not via **kwargs.
            named_kwargs: dict[str, Any] = {}
            chunked = payload.get("chunked")
            if chunked is not None:
                named_kwargs["chunked"] = bool(chunked)
            for key in ("chunk_size", "segment_gap", "crossfade"):
                value = payload.get(key)
                if value is not None:
                    named_kwargs[key] = value

            loop = asyncio.get_running_loop()

            def _synthesize(language: str | None) -> None:
                from voicecli.utils import UNRESTRICTED

                kw = dict(optional_kwargs)
                if language is not None:
                    kw["language"] = language
                api.generate(
                    text,
                    engine=engine,
                    output=out_path,
                    allowed_base=UNRESTRICTED,
                    **kw,
                    **named_kwargs,
                )

            try:
                await loop.run_in_executor(self._executor, _synthesize, None)
            except ValueError as exc:
                # ADR-044 fallback_language semantics: api.generate raises ValueError for
                # param/language validation; retry once with the fallback before giving up.
                fallback_language = payload.get("fallback_language")
                primary_language = payload.get("language")
                if fallback_language and fallback_language != primary_language:
                    log.warning(
                        "language_synthesis_failed_retrying_with_fallback",
                        extra={
                            "request_id": request_id,
                            "primary_language": primary_language,
                            "fallback_language": fallback_language,
                            "error": str(exc),
                        },
                    )
                    await loop.run_in_executor(self._executor, _synthesize, fallback_language)
                else:
                    raise

            # If the engine ran in chunked mode it writes {stem}_NNN.wav files
            # plus a {stem}.done sentinel instead of {stem}.wav directly.
            # Detect and concatenate chunks into out_path before encoding.
            chunks = _collect_chunked_output(out_path)
            if chunks:
                _concat_wav_chunks(chunks, out_path)
                _cleanup_chunks(out_path, chunks)

            audio_b64 = base64.b64encode(out_path.read_bytes()).decode("ascii")
            duration_ms = _wav_duration_ms(out_path)
            waveform_b64 = _wav_waveform_b64(out_path)
            reply_fields: dict[str, Any] = {
                "audio_b64": audio_b64,
                "mime_type": "audio/wav",
                "duration_ms": duration_ms,
            }
            if waveform_b64 is not None:
                reply_fields["waveform_b64"] = waveform_b64
            await self.reply(
                msg,
                encode_reply(build_reply(ok=True, request_id=request_id, **reply_fields)),
            )
        except Exception:
            log.exception("synthesis_failed", extra={"request_id": request_id})
            await self.reply(
                msg,
                encode_reply(
                    build_reply(ok=False, request_id=request_id, error="synthesis_failed")
                ),
            )
        finally:
            cleanup(out_path)
