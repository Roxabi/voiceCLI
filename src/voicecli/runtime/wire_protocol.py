"""Wire protocol and request handling for the VoiceCLI daemon.

Separated from daemon.py to keep daemon.py under 300 lines.
All functions here are private to the daemon package — do not import
from outside voicecli.daemon / voicecli.daemon_protocol.
"""

from __future__ import annotations

import json
import math
import socket
from pathlib import Path

from roxabi_nats import sanitize_for_wire

from voicecli.engines.engine import QWEN_ENGINES


_STR_MAX = 256
_TEXT_MAX = 100_000
DEFAULT_MAX_MSG = 262_144  # 256 KB — covers _TEXT_MAX 100k + envelope

# Patchable in tests — must stay in sync with daemon._OUTPUT_BASE
_OUTPUT_BASE = Path.home()


# ── Wire protocol ─────────────────────────────────────────────────────────────


def send_json(sock: socket.socket, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False) + "\n"
    sock.sendall(payload.encode())


def recv_json(sock: socket.socket, max_msg: int | None = DEFAULT_MAX_MSG) -> dict:
    """Read a newline-terminated JSON message from `sock`.

    `max_msg`: optional cap on buffered bytes; protects against a peer that
    never sends a newline. Pass None to disable; default 256 KB.
    """
    buf = bytearray()
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf.extend(chunk)
        if b"\n" in buf or (max_msg is not None and len(buf) >= max_msg):
            break
    line = buf.split(b"\n")[0]
    return json.loads(line)


# ── Request validation ────────────────────────────────────────────────────────


def _sanitize_str_fields(req: dict, fields: list[tuple[str, int]]) -> str | None:
    """Strip newlines from string fields; return error message or None."""
    for field, max_len in fields:
        val = req.get(field)
        if val is not None and isinstance(val, str):
            if len(val) > max_len:
                return f"{field} exceeds maximum length ({max_len} chars)"
            req[field] = val.replace("\n", "").replace("\r", "")
    return None


def _sanitize_float_fields(
    req: dict, fields: list[tuple[str, float, float]], prefix: str = ""
) -> str | None:
    for field, lo, hi in fields:
        val = req.get(field)
        if val is not None:
            try:
                val = float(val)
            except (TypeError, ValueError):
                return f"{prefix}{field} must be a number"
            if math.isnan(val) or math.isinf(val):
                return f"{prefix}{field} must be finite"
            if not (lo <= val <= hi):
                return f"{prefix}{field} must be between {lo} and {hi}, got {val}"
            req[field] = val
    return None


def _sanitize_int_fields(
    req: dict, fields: list[tuple[str, int, int]], prefix: str = ""
) -> str | None:
    for field, lo, hi in fields:
        val = req.get(field)
        if val is not None:
            try:
                val = int(val)
            except (TypeError, ValueError):
                return f"{prefix}{field} must be an integer"
            if not (lo <= val <= hi):
                return f"{prefix}{field} must be between {lo} and {hi}, got {val}"
            req[field] = val
    return None


def _sanitize_segment(seg: dict, i: int) -> str | None:
    """Validate and sanitize one segment dict in-place. Returns error or None."""
    _SEG_STR_FIELDS = [
        ("instruct", _STR_MAX),
        ("language", _STR_MAX),
        ("voice", _STR_MAX),
        ("accent", _STR_MAX),
        ("personality", _STR_MAX),
        ("speed", _STR_MAX),
        ("emotion", _STR_MAX),
    ]
    err = _sanitize_str_fields(seg, _SEG_STR_FIELDS)
    if err:
        return f"segments[{i}].{err}"

    st = seg.get("text")
    if isinstance(st, str):
        if len(st) > _TEXT_MAX:
            return f"segments[{i}].text exceeds maximum length ({_TEXT_MAX} chars)"
        seg["text"] = st.replace("\n", " ").replace("\r", "")

    err = _sanitize_float_fields(
        seg, [("exaggeration", 0.0, 2.0), ("cfg_weight", 0.0, 1.0)], prefix=f"segments[{i}]."
    )
    if err:
        return err

    err = _sanitize_int_fields(
        seg,
        [("segment_gap", 0, 30_000), ("crossfade", 0, 10_000)],
        prefix=f"segments[{i}].",
    )
    return err


def sanitize_request(req: dict) -> str | None:
    """Validate and sanitize daemon request fields.

    Returns an error message string if validation fails, None if OK.
    Mutates req in-place to strip newlines from string fields.
    """
    _TOP_STR_FIELDS = [
        ("engine", 64),
        ("voice", _STR_MAX),
        ("language", _STR_MAX),
        ("instruct", _STR_MAX),
        ("ref_text", _STR_MAX),
    ]
    err = _sanitize_str_fields(req, _TOP_STR_FIELDS)
    if err:
        return err

    text = req.get("text")
    if isinstance(text, str) and len(text) > _TEXT_MAX:
        return f"text exceeds maximum length ({_TEXT_MAX} chars)"
    if isinstance(text, str):
        req["text"] = text.replace("\n", " ").replace("\r", "")

    err = _sanitize_float_fields(req, [("exaggeration", 0.0, 2.0), ("cfg_weight", 0.0, 1.0)])
    if err:
        return err

    err = _sanitize_int_fields(req, [("segment_gap", 0, 30_000), ("crossfade", 0, 10_000)])
    if err:
        return err

    segments = req.get("segments")
    if isinstance(segments, list):
        for i, seg in enumerate(segments):
            if not isinstance(seg, dict):
                return f"segments[{i}] must be a dict"
            err = _sanitize_segment(seg, i)
            if err:
                return err

    return None


# ── Engine helpers ────────────────────────────────────────────────────────────


def _load_engine(name: str, fast: bool = False):
    from voicecli.engines.engine import get_engine

    eng = get_engine(name)
    if fast and name in QWEN_ENGINES:
        eng.set_small_mode()  # pyright: ignore[reportAttributeAccessIssue]  # Qwen-only
    return eng


def _has_vram(eng_name: str) -> bool:
    """Return True if enough free VRAM is available to load the engine."""
    from voicecli.engines.engine import check_vram

    try:
        check_vram(eng_name)
        return True
    except RuntimeError:
        return False


# ── Job handler ───────────────────────────────────────────────────────────────


def handle_job(
    conn: socket.socket,
    req: dict,
    engines: dict,
    fast: bool = False,
    *,
    load_engine_fn=None,
    has_vram_fn=None,
    output_base: Path | None = None,
) -> None:
    """Process one synthesis job. Called exclusively from the worker thread.

    load_engine_fn / has_vram_fn / output_base are injectable for tests;
    defaults fall back to the module-level implementations.
    """
    _lef = load_engine_fn if load_engine_fn is not None else _load_engine
    _hvf = has_vram_fn if has_vram_fn is not None else _has_vram
    _ob = output_base if output_base is not None else _OUTPUT_BASE

    try:
        error = sanitize_request(req)
        if error:
            send_json(conn, {"status": "error", "message": error})
            return

        action = req.get("action")

        eng_name = req.get("engine")
        if not eng_name:
            send_json(conn, {"status": "error", "message": "missing 'engine' field"})
            return

        if eng_name not in engines:
            if not _hvf(eng_name):
                loaded = list(engines.keys())
                send_json(
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
            engines[eng_name] = _lef(eng_name, fast)

        eng = engines[eng_name]
        text = req.get("text")
        if not text:
            send_json(conn, {"status": "error", "message": "missing required field: 'text'"})
            return

        output_path_str = req.get("output_path")
        if not output_path_str:
            send_json(
                conn,
                {"status": "error", "message": "missing required field: 'output_path'"},
            )
            return

        output_path = Path(output_path_str).resolve()
        if not str(output_path).startswith(str(_ob)):
            send_json(
                conn,
                {"status": "error", "message": "output_path must be within home directory"},
            )
            return

        voice = req.get("voice")
        language = req.get("language")

        from voicecli.api.markdown import Segment

        segments_data = req.get("segments") or []
        segments = [Segment(**s) for s in segments_data]

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
                send_json(conn, {"status": "error", "message": "clone requires ref_audio"})
                return
            ref_audio_path = Path(ref_audio).resolve()
            if not str(ref_audio_path).startswith(str(_ob)):
                send_json(
                    conn,
                    {"status": "error", "message": "ref_audio must be within home directory"},
                )
                return
            ref_text = req.get("ref_text")
            result = eng.clone(text, ref_audio_path, output_path, ref_text=ref_text, **kwargs)
        else:
            send_json(conn, {"status": "error", "message": f"Unknown action: {action!r}"})
            return

        send_json(conn, {"status": "ok", "path": str(result)})

    except Exception as exc:
        try:
            send_json(conn, {"status": "error", "message": sanitize_for_wire(exc)})
        except Exception as send_exc:
            print(
                f"[voicecli daemon] warning: failed to send error response: {send_exc}",
                flush=True,
            )
    finally:
        conn.close()
