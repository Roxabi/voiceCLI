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
            _vram_cleanup()


def _vram_cleanup() -> None:
    """Release cached VRAM allocations after a job completes."""
    import gc

    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _has_vram(eng_name: str) -> bool:
    """Return True if enough free VRAM is available to load the engine."""
    from voicecli.engine import check_vram

    try:
        check_vram(eng_name)
        return True
    except RuntimeError:
        return False


def _sanitize_request(req: dict) -> str | None:
    """Validate and sanitize daemon request fields.

    Returns an error message string if validation fails, None if OK.
    Mutates req in-place to strip newlines from string fields.
    """
    import math

    _STR_MAX = 256
    _TEXT_MAX = 100_000

    # Sanitize string fields: cap length, strip newlines (protocol safety)
    for field, max_len in [
        ("engine", 64),
        ("voice", _STR_MAX),
        ("language", _STR_MAX),
        ("instruct", _STR_MAX),
        ("ref_text", _STR_MAX),
    ]:
        val = req.get(field)
        if val is not None and isinstance(val, str):
            if len(val) > max_len:
                return f"{field} exceeds maximum length ({max_len} chars)"
            req[field] = val.replace("\n", "").replace("\r", "")

    # Text length
    text = req.get("text")
    if isinstance(text, str) and len(text) > _TEXT_MAX:
        return f"text exceeds maximum length ({_TEXT_MAX} chars)"
    if isinstance(text, str):
        req["text"] = text.replace("\n", " ").replace("\r", "")

    # Float range validation
    for field, lo, hi in [("exaggeration", 0.0, 2.0), ("cfg_weight", 0.0, 1.0)]:
        val = req.get(field)
        if val is not None:
            try:
                val = float(val)
            except (TypeError, ValueError):
                return f"{field} must be a number"
            if math.isnan(val) or math.isinf(val):
                return f"{field} must be finite"
            if not (lo <= val <= hi):
                return f"{field} must be between {lo} and {hi}, got {val}"
            req[field] = val

    # Integer range validation
    for field, lo, hi in [("segment_gap", 0, 30_000), ("crossfade", 0, 10_000)]:
        val = req.get(field)
        if val is not None:
            try:
                val = int(val)
            except (TypeError, ValueError):
                return f"{field} must be an integer"
            if not (lo <= val <= hi):
                return f"{field} must be between {lo} and {hi}, got {val}"
            req[field] = val

    # Sanitize per-segment fields
    segments = req.get("segments")
    if isinstance(segments, list):
        for i, seg in enumerate(segments):
            if not isinstance(seg, dict):
                return f"segments[{i}] must be a dict"
            for sf, max_len in [
                ("instruct", _STR_MAX),
                ("language", _STR_MAX),
                ("voice", _STR_MAX),
                ("accent", _STR_MAX),
                ("personality", _STR_MAX),
                ("speed", _STR_MAX),
                ("emotion", _STR_MAX),
            ]:
                sv = seg.get(sf)
                if sv is not None and isinstance(sv, str):
                    if len(sv) > max_len:
                        return f"segments[{i}].{sf} exceeds maximum length ({max_len} chars)"
                    seg[sf] = sv.replace("\n", "").replace("\r", "")
            # Per-segment text
            st = seg.get("text")
            if isinstance(st, str):
                if len(st) > _TEXT_MAX:
                    return f"segments[{i}].text exceeds maximum length ({_TEXT_MAX} chars)"
                seg["text"] = st.replace("\n", " ").replace("\r", "")
            # Per-segment float fields
            for sf, lo, hi in [("exaggeration", 0.0, 2.0), ("cfg_weight", 0.0, 1.0)]:
                sv = seg.get(sf)
                if sv is not None:
                    try:
                        sv = float(sv)
                    except (TypeError, ValueError):
                        return f"segments[{i}].{sf} must be a number"
                    if math.isnan(sv) or math.isinf(sv):
                        return f"segments[{i}].{sf} must be finite"
                    if not (lo <= sv <= hi):
                        return f"segments[{i}].{sf} must be between {lo} and {hi}, got {sv}"
                    seg[sf] = sv
            # Per-segment int fields
            for sf, lo, hi in [("segment_gap", 0, 30_000), ("crossfade", 0, 10_000)]:
                sv = seg.get(sf)
                if sv is not None:
                    try:
                        sv = int(sv)
                    except (TypeError, ValueError):
                        return f"segments[{i}].{sf} must be an integer"
                    if not (lo <= sv <= hi):
                        return f"segments[{i}].{sf} must be between {lo} and {hi}, got {sv}"
                    seg[sf] = sv

    return None


def _handle_job(conn: socket.socket, req: dict, engines: dict, fast: bool = False) -> None:
    """Process one synthesis job. Called exclusively from the worker thread."""
    try:
        # Validate and sanitize request fields
        error = _sanitize_request(req)
        if error:
            _send_json(conn, {"status": "error", "message": error})
            return

        action = req.get("action")

        eng_name = req.get("engine")
        if not eng_name:
            _send_json(conn, {"status": "error", "message": "missing 'engine' field"})
            return

        if eng_name not in engines:
            if not _has_vram(eng_name):
                loaded = list(engines.keys())
                _send_json(
                    conn,
                    {
                        "status": "error",
                        "message": (
                            f"Insufficient VRAM to load '{eng_name}'. Already loaded: {loaded}"
                        ),
                    },
                )
                return
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
                conn,
                {"status": "error", "message": "missing required field: 'output_path'"},
            )
            return
        output_path = Path(output_path_str).resolve()
        if not str(output_path).startswith(str(_OUTPUT_BASE)):
            _send_json(
                conn,
                {
                    "status": "error",
                    "message": "output_path must be within home directory",
                },
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
            ref_audio_path = Path(ref_audio).resolve()
            if not str(ref_audio_path).startswith(str(_OUTPUT_BASE)):
                _send_json(
                    conn,
                    {"status": "error", "message": "ref_audio must be within home directory"},
                )
                return
            ref_text = req.get("ref_text")
            result = eng.clone(text, ref_audio_path, output_path, ref_text=ref_text, **kwargs)
        else:
            _send_json(conn, {"status": "error", "message": f"Unknown action: {action!r}"})
            return

        _send_json(conn, {"status": "ok", "path": str(result)})

    except Exception as exc:
        try:
            _send_json(conn, {"status": "error", "message": str(exc)})
        except Exception as send_exc:
            print(
                f"[voicecli daemon] warning: failed to send error response: {send_exc}",
                flush=True,
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
