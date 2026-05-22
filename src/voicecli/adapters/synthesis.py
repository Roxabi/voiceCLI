"""Infrastructure adapters for SynthesisPort.

Provides two concrete implementations:
- DaemonSynthesisAdapter: routes requests through the voicecli daemon socket.
- LocalSynthesisAdapter: calls engines directly via ModelRegistry.

All imports of heavy dependencies (voicecli.daemon, voicecli.engine, torch) are
deferred to method bodies — never at class or module level.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

_DAEMON_WAIT_SECS = 60.0
_DAEMON_POLL_INTERVAL = 2.0


class DaemonSynthesisAdapter:
    """SynthesisPort implementation that dispatches through the daemon socket."""

    # ── availability ─────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Return True if the daemon socket path exists."""
        from voicecli.daemon import SOCKET_PATH

        return SOCKET_PATH.exists()

    # ── internal helpers ──────────────────────────────────────────────────────

    def _wait_for_socket(self, timeout: float = _DAEMON_WAIT_SECS) -> bool:
        """Block until daemon socket appears or timeout expires."""
        from voicecli.daemon import SOCKET_PATH

        if SOCKET_PATH.exists():
            return True
        print(
            f"[voicecli] daemon socket not found — waiting up to {timeout:.0f}s...",
            flush=True,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(_DAEMON_POLL_INTERVAL)
            if SOCKET_PATH.exists():
                return True
        return False

    def _try_socket(self, request: dict) -> Path | None:
        """Send request to daemon. Returns WAV path on success, None on failure."""
        from voicecli.daemon import SOCKET_PATH, daemon_request

        if not SOCKET_PATH.exists():
            return None
        try:
            resp = daemon_request(request, timeout=300)
            if resp.get("status") == "ok":
                return Path(resp["path"])
            log.error("daemon error: %s", resp.get("message", "unknown error"))
        except Exception:
            log.exception("daemon request failed")
        return None

    def _chunk_fn(self, engine_name: str):
        """Return a daemon_fn closure for chunked generation/cloning."""

        def daemon_fn(method: str, text: str, voice, chunk_path: Path, **kwargs) -> bool:
            req: dict = {
                "action": method,
                "engine": engine_name,
                "text": text,
                "voice": voice,
                "output_path": str(chunk_path.resolve()),
                "language": kwargs.get("language"),
                "instruct": kwargs.get("instruct"),
                "exaggeration": kwargs.get("exaggeration"),
                "cfg_weight": kwargs.get("cfg_weight"),
                "segment_gap": kwargs.get("segment_gap"),
                "crossfade": kwargs.get("crossfade"),
                "segments": [],
            }
            if method == "clone":
                ref = kwargs.get("ref_audio")
                req["ref_audio"] = str(ref.resolve()) if ref else None
                req["ref_text"] = kwargs.get("ref_text")
            if not self._wait_for_socket():
                return False
            if self._try_socket(req) is None:
                return False
            return True

        return daemon_fn

    # ── SynthesisPort interface ───────────────────────────────────────────────

    def generate(
        self,
        engine: str,
        text: str,
        voice: str | None,
        output_path: Path,
        *,
        language: str | None = None,
        instruct: str | None = None,
        exaggeration: float | None = None,
        cfg_weight: float | None = None,
        segment_gap: int | None = None,
        crossfade: int | None = None,
        segments: list | None = None,
        **kwargs,
    ) -> Path | None:
        """Generate speech via the daemon socket, fallback to local engine on miss."""
        from voicecli.engines.engine import QWEN_ENGINES, get_engine

        # Try socket first for Qwen engines
        if engine in QWEN_ENGINES and self._wait_for_socket():
            result = self._try_socket(
                {
                    "action": "generate",
                    "engine": engine,
                    "text": text,
                    "voice": voice,
                    "output_path": str(output_path.resolve()),
                    "language": language,
                    "instruct": instruct,
                    "exaggeration": exaggeration,
                    "cfg_weight": cfg_weight,
                    "segment_gap": segment_gap,
                    "crossfade": crossfade,
                    "segments": segments or [],
                }
            )
            if result is not None:
                return result

        # Fallback to local engine
        eng = get_engine(engine)
        if kwargs.pop("fast", False) and engine in QWEN_ENGINES:
            eng.set_small_mode()  # pyright: ignore[reportAttributeAccessIssue]  # Qwen-only
        return eng.generate(
            text,
            voice,
            output_path,
            language=language,
            instruct=instruct,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
            segment_gap=segment_gap,
            crossfade=crossfade,
            segments=segments,
            **kwargs,
        )

    def clone(
        self,
        engine: str,
        text: str,
        ref_audio: Path,
        output_path: Path,
        *,
        ref_text: str | None = None,
        language: str | None = None,
        exaggeration: float | None = None,
        cfg_weight: float | None = None,
        segment_gap: int | None = None,
        crossfade: int | None = None,
        segments: list | None = None,
        **kwargs,
    ) -> Path | None:
        """Clone voice via the daemon socket, fallback to local engine on miss."""
        from voicecli.engines.engine import QWEN_ENGINES, get_engine

        # Try socket first for Qwen engines
        if engine in QWEN_ENGINES and self._wait_for_socket():
            result = self._try_socket(
                {
                    "action": "clone",
                    "engine": engine,
                    "text": text,
                    "voice": None,
                    "ref_audio": str(ref_audio.resolve()),
                    "ref_text": ref_text,
                    "output_path": str(output_path.resolve()),
                    "language": language,
                    "exaggeration": exaggeration,
                    "cfg_weight": cfg_weight,
                    "segment_gap": segment_gap,
                    "crossfade": crossfade,
                    "segments": segments or [],
                }
            )
            if result is not None:
                return result

        # Fallback to local engine
        eng = get_engine(engine)
        if kwargs.pop("fast", False) and engine in QWEN_ENGINES:
            eng.set_small_mode()  # pyright: ignore[reportAttributeAccessIssue]  # Qwen-only
        return eng.clone(
            text,
            ref_audio,
            output_path,
            ref_text=ref_text,
            language=language,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
            segment_gap=segment_gap,
            crossfade=crossfade,
            segments=segments,
            **kwargs,
        )


class LocalSynthesisAdapter:
    """SynthesisPort implementation that calls engines directly via ModelRegistry."""

    def __init__(self, registry) -> None:
        self._registry = registry

    def is_available(self) -> bool:
        """Always available — local engines need no external socket."""
        return True

    def generate(
        self,
        engine: str,
        text: str,
        voice: str | None,
        output_path: Path,
        *,
        language: str | None = None,
        instruct: str | None = None,
        exaggeration: float | None = None,
        cfg_weight: float | None = None,
        segment_gap: int | None = None,
        crossfade: int | None = None,
        segments: list | None = None,
        **kwargs,
    ) -> Path | None:
        """Generate speech using a registry-cached local engine."""
        from voicecli.engines.engine import QWEN_ENGINES

        eng = self._registry.get(engine)
        if kwargs.pop("fast", False) and engine in QWEN_ENGINES:
            eng.set_small_mode()  # pyright: ignore[reportAttributeAccessIssue]  # Qwen-only
        return eng.generate(
            text,
            voice,
            output_path,
            language=language,
            instruct=instruct,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
            segment_gap=segment_gap,
            crossfade=crossfade,
            segments=segments,
            **kwargs,
        )

    def clone(
        self,
        engine: str,
        text: str,
        ref_audio: Path,
        output_path: Path,
        *,
        ref_text: str | None = None,
        language: str | None = None,
        exaggeration: float | None = None,
        cfg_weight: float | None = None,
        segment_gap: int | None = None,
        crossfade: int | None = None,
        segments: list | None = None,
        **kwargs,
    ) -> Path | None:
        """Clone voice using a registry-cached local engine."""
        from voicecli.engines.engine import QWEN_ENGINES

        eng = self._registry.get(engine)
        if kwargs.pop("fast", False) and engine in QWEN_ENGINES:
            eng.set_small_mode()  # pyright: ignore[reportAttributeAccessIssue]  # Qwen-only
        return eng.clone(
            text,
            ref_audio,
            output_path,
            ref_text=ref_text,
            language=language,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
            segment_gap=segment_gap,
            crossfade=crossfade,
            segments=segments,
            **kwargs,
        )
