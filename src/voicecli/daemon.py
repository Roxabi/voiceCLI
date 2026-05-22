"""Daemon server and client for keeping Qwen TTS models warm in VRAM.

Protocol: newline-delimited JSON over AF_UNIX SOCK_STREAM.
Socket path: ~/.local/share/voicecli/daemon.sock

Actions:
  ping     — liveness check
  generate — synthesise text using a built-in voice
  clone    — synthesise text cloning a reference voice
"""

from __future__ import annotations

import gc
import os
import queue
import socket
import threading
from dataclasses import dataclass
from pathlib import Path

from voicecli.daemon_protocol import (
    handle_job,
    recv_json,
    sanitize_request as _sanitize_request,  # noqa: F401
    send_json,
)
from voicecli.engines.engine import QWEN_ENGINES
from voicecli.paths import TTS_SOCKET_PATH as SOCKET_PATH

_OUTPUT_BASE = Path.home()  # output_path must resolve within this directory (patchable in tests)
_DEFAULT_TIMEOUT = 300  # seconds


@dataclass
class _Job:
    conn: socket.socket
    req: dict


# ── Public client API ─────────────────────────────────────────────────────────


def daemon_request(request: dict, timeout: int = _DEFAULT_TIMEOUT) -> dict:
    """Send a JSON request to the daemon and return the response dict."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(SOCKET_PATH))
        send_json(sock, request)
        return recv_json(sock)
    finally:
        sock.close()


# ── Server ────────────────────────────────────────────────────────────────────


def daemon_main(preload: str | None = None, fast: bool = False) -> None:
    """Start the daemon, optionally preloading an engine at startup.

    Args:
        preload: Engine name to load immediately (e.g. "qwen", "qwen-fast").
        fast:    If True, use the smaller Qwen model for all Qwen engines.
    """
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOCKET_PATH.unlink(missing_ok=True)

    engines: dict[str, object] = {}
    if preload:
        print(f"[voicecli daemon] Preloading {preload}...", flush=True)
        engines[preload] = _load_engine(preload, fast)

    _queue: queue.Queue = queue.Queue()
    threading.Thread(target=_worker, args=(_queue, engines, fast), daemon=True).start()

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as srv:
        srv.bind(str(SOCKET_PATH))
        os.chmod(str(SOCKET_PATH), 0o600)
        srv.listen(5)
        print(f"[voicecli daemon] Ready on {SOCKET_PATH}", flush=True)
        try:
            while True:
                conn, _ = srv.accept()
                conn.settimeout(5)
                try:
                    req = recv_json(conn)
                except Exception:
                    conn.close()
                    continue
                if req.get("action") == "ping":
                    send_json(conn, {"status": "ok"})
                    conn.close()
                else:
                    # conn ownership transfers to worker — main thread must not touch conn after this
                    _queue.put(_Job(conn=conn, req=req))
        except KeyboardInterrupt:
            pass
        finally:
            SOCKET_PATH.unlink(missing_ok=True)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _load_engine(name: str, fast: bool = False):
    from voicecli.engines.engine import get_engine

    eng = get_engine(name)
    if fast and name in QWEN_ENGINES:
        eng._small = True
    return eng


def _has_vram(eng_name: str) -> bool:
    """Return True if enough free VRAM is available to load the engine."""
    from voicecli.engines.engine import check_vram

    try:
        check_vram(eng_name)
        return True
    except RuntimeError:
        return False


def _worker(q: queue.Queue, engines: dict, fast: bool) -> None:
    """Single worker thread: drain the job queue and synthesize sequentially."""
    while True:
        job: _Job = q.get()
        try:
            handle_job(
                job.conn,
                job.req,
                engines,
                fast,
                load_engine_fn=_load_engine,
                has_vram_fn=_has_vram,
                output_base=_OUTPUT_BASE,
            )
        finally:
            q.task_done()
            _vram_cleanup()


def _vram_cleanup() -> None:
    """Release cached VRAM allocations after a job completes."""
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
