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
import socket
from pathlib import Path

SOCKET_PATH = Path.home() / ".local" / "share" / "voicecli" / "daemon.sock"
_DEFAULT_TIMEOUT = 300  # seconds


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
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()

    engines: dict[str, object] = {}
    if preload:
        print(f"[voicecli daemon] Preloading {preload}...", flush=True)
        engines[preload] = _load_engine(preload, fast)

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as srv:
        srv.bind(str(SOCKET_PATH))
        srv.listen(5)
        print(f"[voicecli daemon] Ready on {SOCKET_PATH}", flush=True)
        try:
            while True:
                conn, _ = srv.accept()
                _handle(conn, engines, fast)
        except KeyboardInterrupt:
            pass
        finally:
            SOCKET_PATH.unlink(missing_ok=True)


def _load_engine(name: str, fast: bool = False):
    from voicecli.engine import get_engine

    eng = get_engine(name)
    if fast and name in ("qwen", "qwen-fast"):
        eng._small = True
    return eng


def _handle(conn: socket.socket, engines: dict, fast: bool = False) -> None:
    """Process one request synchronously (GPU is single-threaded anyway)."""
    try:
        req = _recv_json(conn)
        action = req.get("action")

        if action == "ping":
            _send_json(conn, {"status": "ok"})
            return

        eng_name = req.get("engine")
        if not eng_name:
            _send_json(conn, {"status": "error", "message": "missing 'engine' field"})
            return

        if eng_name not in engines:
            print(f"[voicecli daemon] Loading {eng_name}...", flush=True)
            engines[eng_name] = _load_engine(eng_name, fast)

        eng = engines[eng_name]
        text = req["text"]
        voice = req.get("voice")
        output_path = Path(req["output_path"])
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
            result = eng.clone(
                text, Path(ref_audio), output_path, ref_text=ref_text, **kwargs
            )
        else:
            _send_json(conn, {"status": "error", "message": f"Unknown action: {action!r}"})
            return

        _send_json(conn, {"status": "ok", "path": str(result)})

    except Exception as exc:
        try:
            _send_json(conn, {"status": "error", "message": str(exc)})
        except Exception:
            pass
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
    line = bytes(buf).split(b"\n")[0]
    return json.loads(line)
