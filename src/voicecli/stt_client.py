"""Client for the STT daemon — toggle, status, notifications, auto-paste, hotkey listener.

Protocol: newline-delimited JSON over AF_UNIX SOCK_STREAM.
Socket path: ~/.local/share/voicecli/stt-daemon.sock
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

SOCKET_PATH = Path.home() / ".local" / "share" / "voicecli" / "stt-daemon.sock"
_DEFAULT_TIMEOUT = 10  # seconds


# ── Wire protocol ─────────────────────────────────────────────────────────────


def _send_request(action: str, timeout: int = _DEFAULT_TIMEOUT, **extra: object) -> dict:
    """Connect to STT daemon, send action, return response dict.

    Returns ``{"status": "error", "message": "STT daemon not running"}`` when the
    socket is absent or the connection is refused.

    Args:
        action: The action string to send.
        timeout: Socket timeout in seconds.
        **extra: Additional fields merged into the JSON payload.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(SOCKET_PATH))
        payload = json.dumps({"action": action, **extra}, ensure_ascii=False) + "\n"
        sock.sendall(payload.encode())
        buf = bytearray()
        max_response = 65536
        while b"\n" not in buf and len(buf) < max_response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf.extend(chunk)
        return json.loads(buf.split(b"\n")[0])
    except (ConnectionRefusedError, FileNotFoundError, OSError, ValueError):
        return {"status": "error", "message": "STT daemon not running"}
    finally:
        sock.close()


def send_toggle(mode: str | None = None) -> dict:
    """Send a toggle action to the STT daemon and return the response dict.

    Args:
        mode: Optional mode name (e.g. "french", "code") to apply for this recording.
    """
    extra: dict[str, object] = {}
    if mode is not None:
        extra["mode"] = mode
    return _send_request("toggle", timeout=60, **extra)


def send_status() -> dict:
    """Send a status action to the STT daemon and return the response dict."""
    return _send_request("status")


def send_cancel() -> dict:
    """Send a cancel action to the STT daemon and return the response dict."""
    return _send_request("cancel")


def send_next_mode() -> dict:
    """Cycle to the next mode and return the response dict."""
    return _send_request("next_mode")


# ── Desktop notifications ─────────────────────────────────────────────────────

# Stable replace-ID so each notify-send call replaces the previous bubble.
# notify-send -r requires an integer; we derive one from the app name.
_NOTIFY_REPLACE_ID = str(abs(hash("voicecli-dictate")) % 65536)


def notify(body: str, timeout: int = 3000) -> None:
    """Show a desktop notification via notify-send.

    Silently skips if notify-send is not installed or any error occurs.
    Uses a fixed replace-ID so each call replaces the previous notification.
    """
    import html
    import shutil
    import subprocess

    if not shutil.which("notify-send"):
        return
    try:
        subprocess.run(
            [
                "notify-send",
                "-r",
                _NOTIFY_REPLACE_ID,
                "VoiceCLI",
                html.escape(body),
                "-t",
                str(timeout),
            ],
            check=False,
            capture_output=True,
        )
    except Exception:
        pass


# ── Auto-paste ────────────────────────────────────────────────────────────────


def auto_paste(text: str) -> None:
    """Type *text* into the focused window via xdotool.

    Waits 150 ms first so the caller's window can regain focus after the hotkey
    is released.  Silently skips if xdotool is not installed or any error occurs.
    """
    import shutil
    import subprocess
    import time

    if not shutil.which("xdotool"):
        return
    try:
        time.sleep(0.15)
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--", text],
            check=False,
            capture_output=True,
        )
    except Exception:
        pass


# ── Hotkey listener ───────────────────────────────────────────────────────────


def hotkey_loop(hotkey: str = "ctrl+space", paste: bool = False) -> None:
    """Block until Ctrl+C, firing send_toggle() on each *hotkey* press.

    Applies a 300 ms debounce to avoid double-triggers.  Calls ``notify()`` and
    optionally ``auto_paste()`` based on the daemon response.

    Args:
        hotkey: Key combination in pynput format pieces separated by ``+``
                (e.g. ``"ctrl+space"``, ``"ctrl+shift+d"``).
        paste:  If True, type transcribed text into the focused window.
    """
    import re
    import sys

    if not re.fullmatch(r"[a-z0-9_]+(\+[a-z0-9_]+)*", hotkey.lower()):
        print(f"Invalid hotkey format: {hotkey!r}", file=sys.stderr)
        return

    from pynput import keyboard
    import time

    last_trigger = 0.0
    debounce_s = 0.3

    # Convert "alt+space" → "<alt>+<space>", "ctrl+shift+d" → "<ctrl>+<shift>+d".
    # Named keys (modifiers + special) get <...>; single printable chars stay bare.
    _NAMED_KEYS = frozenset(
        {
            "alt",
            "alt_l",
            "alt_r",
            "ctrl",
            "ctrl_l",
            "ctrl_r",
            "shift",
            "shift_l",
            "shift_r",
            "cmd",
            "cmd_l",
            "cmd_r",
            "space",
            "enter",
            "return",
            "tab",
            "esc",
            "escape",
            "backspace",
            "delete",
            "insert",
            "home",
            "end",
            "page_up",
            "page_down",
            "up",
            "down",
            "left",
            "right",
            "caps_lock",
            "num_lock",
            "scroll_lock",
            "print_screen",
            "f1",
            "f2",
            "f3",
            "f4",
            "f5",
            "f6",
            "f7",
            "f8",
            "f9",
            "f10",
            "f11",
            "f12",
        }
    )

    def _to_pynput(combo: str) -> str:
        parts = combo.lower().split("+")
        wrapped = [f"<{p}>" if p in _NAMED_KEYS else p for p in parts]
        return "+".join(wrapped)

    hotkey_pynput = _to_pynput(hotkey)

    def on_hotkey() -> None:
        nonlocal last_trigger
        now = time.monotonic()
        if now - last_trigger < debounce_s:
            return
        last_trigger = now

        resp = send_toggle()

        if resp.get("status") == "error":
            notify(resp.get("message", "STT daemon not running"), timeout=3000)
            return

        state = resp.get("state", "")
        text = resp.get("text", "")
        language = resp.get("language") or ""

        if state == "recording":
            notify("Recording...", timeout=0)
        elif state == "idle" and text:
            preview = text[:50] + ("..." if len(text) > 50 else "")
            lang_tag = f"[{language}] " if language else ""
            notify(f"{lang_tag}{preview}", timeout=3000)
            if paste:
                auto_paste(text)
        elif state == "idle":
            notify("No speech detected", timeout=2000)
        elif state == "queued":
            notify("Queued...", timeout=3000)
        else:
            notify(state, timeout=2000)

    with keyboard.GlobalHotKeys({hotkey_pynput: on_hotkey}) as listener:
        print(f"Listening for {hotkey}... (Ctrl+C to stop)", flush=True)
        try:
            listener.join()
        except KeyboardInterrupt:
            pass
