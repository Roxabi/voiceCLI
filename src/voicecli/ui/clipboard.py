"""Clipboard read/write and auto-paste utilities.

Platform support: wl-copy (Wayland), xclip/xsel (X11), clip.exe (WSL2),
wtype (Wayland paste), xdotool (X11 paste), AHK trigger (WSL2 paste).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _is_wsl() -> bool:
    return "WSL_DISTRO_NAME" in os.environ or (
        Path("/proc/version").exists() and "microsoft" in Path("/proc/version").read_text().lower()
    )


def write_clipboard(text: str) -> None:
    """Write *text* to the system clipboard.

    Tries wl-copy → xclip → xsel → clip.exe in order; prints a helpful
    install suggestion to stderr if none are available.
    """
    import shutil
    import subprocess

    for cmd in [
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["clip.exe"],
    ]:
        if shutil.which(cmd[0]):
            try:
                encoding = "utf-16-le" if cmd[0] == "clip.exe" else "utf-8"
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                proc.communicate(input=text.encode(encoding))
                if proc.returncode == 0:
                    return
            except Exception:
                pass

    # Build a helpful install suggestion based on environment
    if _is_wsl():
        suggestion = (
            "clip.exe is built-in on WSL2 — check WSL_INTEROP is set, or: sudo apt install xclip"
        )
    elif shutil.which("apt"):
        suggestion = "sudo apt install wl-clipboard"
    elif shutil.which("dnf"):
        suggestion = "sudo dnf install wl-clipboard"
    elif shutil.which("pacman"):
        suggestion = "sudo pacman -S wl-clipboard"
    else:
        suggestion = "install wl-clipboard or xclip"
    print(
        f"[stt] clipboard write failed: no wl-copy/xclip/xsel/clip.exe found — {suggestion}",
        file=sys.stderr,
    )


def auto_paste() -> None:
    """Trigger a Ctrl+V paste in the active window.

    On WSL2: writes a flag file that the AHK script polls every 150 ms.
    AHK then sends ^+v natively on the Windows side — no powershell startup lag,
    no foreground-window race conditions.

    Fallback order: AHK trigger (WSL2) → wtype (Wayland) → xdotool (X11).
    """
    import subprocess
    import time

    time.sleep(0.15)  # small grace period so overlay close is processed first

    if _is_wsl():
        # Resolve Windows %TEMP% → WSL path and drop the trigger file
        try:
            r = subprocess.run(
                ["cmd.exe", "/c", "echo %TEMP%"],
                capture_output=True,
                timeout=3,
            )
            win_path = r.stdout.decode(
                "cp850", errors="replace"
            ).strip()  # e.g. C:\Users\Mickael\AppData\Local\Temp
            if len(win_path) >= 3 and win_path[1] == ":":
                drive = win_path[0].lower()
                rest = win_path[2:].replace("\\", "/")
                trigger = Path(f"/mnt/{drive}{rest}/voicecli_paste_trigger")
                trigger.write_text("1")
                print("[stt] auto-paste: trigger written for AHK", file=sys.stderr)
                return
        except Exception as e:
            print(f"[stt] auto-paste AHK trigger failed: {e}", file=sys.stderr)

    import shutil

    if shutil.which("wtype"):
        try:
            subprocess.Popen(
                ["wtype", "-M", "ctrl", "-P", "v", "-p", "v", "-m", "ctrl"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except Exception as e:
            print(f"[stt] auto-paste wtype failed: {e}", file=sys.stderr)

    if shutil.which("xdotool"):
        try:
            subprocess.Popen(
                ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except Exception as e:
            print(f"[stt] auto-paste xdotool failed: {e}", file=sys.stderr)

    print(
        "[stt] auto-paste: no suitable method (need wtype, xdotool, or AHK trigger)",
        file=sys.stderr,
    )
