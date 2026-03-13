"""Daemon server and client for keeping Qwen TTS models warm in VRAM.

Protocol: newline-delimited JSON over AF_UNIX SOCK_STREAM.
Socket path: ~/.local/share/voicecli/daemon.sock

Actions:
  ping     — liveness check
  generate — synthesise text using a built-in voice
  clone    — synthesise text cloning a reference voice
"""

from __future__ import annotations

import json
import os
import queue
import socket
import threading
from dataclasses import dataclass
from pathlib import Path

from voicecli.engine import QWEN_ENGINES

SOCKET_PATH = Path.home() / ".local" / "share" / "voicecli" / "daemon.sock"
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
        _send_json(sock, request)
        return _recv_json(sock)
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
                    req = _recv_json(conn)
                except Exception:
                    conn.close()
                    continue
                if req.get("action") == "ping":
                    _send_json(conn, {"status": "ok"})
                    conn.close()
                else:
                    # conn ownership transfers to worker — main thread must not touch conn after this
                    _queue.put(_Job(conn=conn, req=req))
        except KeyboardInterrupt:
            pass
        finally:
            SOCKET_PATH.unlink(missing_ok=True)


def _load_engine(name: str, fast: bool = False):
    from voicecli.engine import get_engine

    eng = get_engine(name)
    if fast and name in QWEN_ENGINES:
        eng._small = True
    return eng


def _worker(q: queue.Queue, engines: dict, fast: bool) -> None:
    """Single worker thread: drain the job queue and synthesize sequentially."""
    while True:
        job: _Job = q.get()
        try:
            _handle_job(job.conn, job.req, engines, fast)
        finally:
            q.task_done()


def _handle_job(conn: socket.socket, req: dict, engines: dict, fast: bool = False) -> None:
    """Process one synthesis job. Called exclusively from the worker thread."""
    try:
        action = req.get("action")

        eng_name = req.get("engine")
        if not eng_name:
            _send_json(conn, {"status": "error", "message": "missing 'engine' field"})
            return

        if eng_name not in engines:
            print(f"[voicecli daemon] Loading {eng_name}...", flush=True)
            engines[eng_name] = _load_engine(eng_name, fast)

        eng = engines[eng_name]
        text = req.get("text")
        if not text:
            _send_json(conn, {"status": "error", "message": "missing required field: 'text'"})
            return
        output_path_str = req.get("output_path")
        if not output_path_str:
            _send_json(
                conn, {"status": "error", "message": "missing required field: 'output_path'"}
            )
            return
        output_path = Path(output_path_str).resolve()
        if not str(output_path).startswith(str(_OUTPUT_BASE)):
            _send_json(
                conn, {"status": "error", "message": "output_path must be within home directory"}
            )
            return
        voice = req.get("voice")
        language = req.get("language")

        # Reconstruct Segment objects from JSON
        from voicecli.markdown import Segment

        segments_data = req.get("segments") or []
        segments = [Segment(**s) for s in segments_data]

        # Build engine kwargs
        kwargs: dict = {}
        for k in ("instruct", "exaggeration", "cfg_weight", "segment_gap", "crossfade"):
            if req.get(k) is not None:
                kwargs[k] = req[k]
        if language:
            kwargs["language"] = language
        if segments:
            kwargs["segments"] = segments

        output_path.parent.mkdir(parents=True, exist_ok=True)

        if action == "generate":
            result = eng.generate(text, voice, output_path, **kwargs)
        elif action == "clone":
            ref_audio = req.get("ref_audio")
            if not ref_audio:
                _send_json(conn, {"status": "error", "message": "clone requires ref_audio"})
                return
            ref_text = req.get("ref_text")
            result = eng.clone(text, Path(ref_audio), output_path, ref_text=ref_text, **kwargs)
        else:
            _send_json(conn, {"status": "error", "message": f"Unknown action: {action!r}"})
            return

        _send_json(conn, {"status": "ok", "path": str(result)})

    except Exception as exc:
        try:
            _send_json(conn, {"status": "error", "message": str(exc)})
        except Exception as send_exc:
            print(
                f"[voicecli daemon] warning: failed to send error response: {send_exc}", flush=True
            )
    finally:
        conn.close()


# ── Wire protocol ─────────────────────────────────────────────────────────────


def _send_json(sock: socket.socket, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False) + "\n"
    sock.sendall(payload.encode())


def _recv_json(sock: socket.socket) -> dict:
    buf = bytearray()
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf.extend(chunk)
        if b"\n" in buf:
            break
    line = buf.split(b"\n")[0]
    return json.loads(line)
