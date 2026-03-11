"""Floating waveform overlay shown during STT recording.

Run as: python -m voicecli.overlay
Closes automatically when the daemon is no longer in 'recording' state.
Press ESC to cancel the recording.

Design: dark rounded panel with symmetric waveform bars (grow ±from centre)
and a bottom toolbar showing mode name + keyboard shortcuts.
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
import threading
import tkinter as tk
from collections import deque
from pathlib import Path

from voicecli.stt_client import SOCKET_PATH, send_cancel, send_next_mode, send_status
from voicecli.stt_daemon import LEVEL_FILE

_ASSETS = Path(__file__).parent / "assets"
_SND_START = _ASSETS / "start.wav"
_SND_STOP = _ASSETS / "stop.wav"


def _play(path: Path) -> None:
    """Play a WAV file non-blocking via paplay (fire-and-forget)."""
    if path.exists():
        subprocess.Popen(
            ["paplay", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )


# ── Layout ────────────────────────────────────────────────────────────────────
BAR_COUNT = 58
BAR_W = 4
BAR_GAP = 2
WAVE_PAD_X = 10
WAVE_H = 56  # height of waveform area
TOOL_H = 26  # height of bottom toolbar
CORNER_R = 10

WIN_W = WAVE_PAD_X * 2 + BAR_COUNT * (BAR_W + BAR_GAP) - BAR_GAP
WIN_H = WAVE_H + TOOL_H

WAVE_CY = WAVE_H // 2  # vertical centre of waveform
BAR_MAX_H = 22  # max half-height of a bar (grows ± from WAVE_CY)

POLL_MS = 200
ANIM_MS = 50  # ~20 fps

LEVEL_PEAK = 0.06

# ── Colors ────────────────────────────────────────────────────────────────────
CORNER_KEY = "#010203"
BG = "#141420"
TOOLBAR_BG = "#0d0d18"
SEP_COLOR = "#252540"
BAR_DIM = (0x28, 0x28, 0x48)  # very dark bar (idle / short)
BAR_BRIGHT = (0xDD, 0xDD, 0xFF)  # near-white bar (active / tall)
BADGE_BG = "#252542"
BADGE_BORDER = "#44446a"
BADGE_FG = "#9999bb"
MODE_FG = "#ffffff"
DOT_COLOR = "#e94560"


def _bar_color(frac: float) -> str:
    """Interpolate BAR_DIM → BAR_BRIGHT by height fraction."""
    t = max(0.0, min(1.0, frac))
    r = int(BAR_DIM[0] + (BAR_BRIGHT[0] - BAR_DIM[0]) * t)
    g = int(BAR_DIM[1] + (BAR_BRIGHT[1] - BAR_DIM[1]) * t)
    b = int(BAR_DIM[2] + (BAR_BRIGHT[2] - BAR_DIM[2]) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _read_level() -> float:
    try:
        return float(LEVEL_FILE.read_text().strip())
    except Exception:
        return 0.0


def _draw_rounded_rect(
    canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, r: int, **kw: object
) -> None:
    kw2 = dict(kw)
    canvas.create_arc(x1, y1, x1 + 2 * r, y1 + 2 * r, start=90, extent=90, style="pieslice", **kw2)  # type: ignore[arg-type]
    canvas.create_arc(x2 - 2 * r, y1, x2, y1 + 2 * r, start=0, extent=90, style="pieslice", **kw2)  # type: ignore[arg-type]
    canvas.create_arc(x2 - 2 * r, y2 - 2 * r, x2, y2, start=270, extent=90, style="pieslice", **kw2)  # type: ignore[arg-type]
    canvas.create_arc(x1, y2 - 2 * r, x1 + 2 * r, y2, start=180, extent=90, style="pieslice", **kw2)  # type: ignore[arg-type]
    canvas.create_rectangle(x1 + r, y1, x2 - r, y2, **kw2)  # type: ignore[arg-type]
    canvas.create_rectangle(x1, y1 + r, x2, y2 - r, **kw2)  # type: ignore[arg-type]


def _draw_badge(canvas: tk.Canvas, x: int, y: int, label: str) -> int:
    """Draw a keyboard-key badge centred at y. Returns right edge x."""
    pad = 5
    w = len(label) * 6 + pad * 2
    h = 14
    x1, y1, x2, y2 = x, y - h // 2, x + w, y + h // 2
    r = 3
    canvas.create_arc(
        x1,
        y1,
        x1 + 2 * r,
        y1 + 2 * r,
        start=90,
        extent=90,
        style="pieslice",
        fill=BADGE_BG,
        outline=BADGE_BORDER,
    )
    canvas.create_arc(
        x2 - 2 * r,
        y1,
        x2,
        y1 + 2 * r,
        start=0,
        extent=90,
        style="pieslice",
        fill=BADGE_BG,
        outline=BADGE_BORDER,
    )
    canvas.create_arc(
        x2 - 2 * r,
        y2 - 2 * r,
        x2,
        y2,
        start=270,
        extent=90,
        style="pieslice",
        fill=BADGE_BG,
        outline=BADGE_BORDER,
    )
    canvas.create_arc(
        x1,
        y2 - 2 * r,
        x1 + 2 * r,
        y2,
        start=180,
        extent=90,
        style="pieslice",
        fill=BADGE_BG,
        outline=BADGE_BORDER,
    )
    canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=BADGE_BG, outline=BADGE_BG)
    canvas.create_rectangle(x1, y1 + r, x2, y2 - r, fill=BADGE_BG, outline=BADGE_BG)
    canvas.create_line(x1 + r, y1, x2 - r, y1, fill=BADGE_BORDER)
    canvas.create_line(x1 + r, y2, x2 - r, y2, fill=BADGE_BORDER)
    canvas.create_text(x + w // 2, y, text=label, font=("monospace", 7), fill=BADGE_FG)
    return x2


class WaveformOverlay:
    def __init__(self, initial_mode: str | None = None, test_mode: bool = False) -> None:
        self._running = True
        self._test_mode = test_mode
        self._audio_level = 0.0

        # Ring buffer of amplitudes (one per bar, scrolls left each frame)
        self._samples: deque[float] = deque([0.0] * BAR_COUNT, maxlen=BAR_COUNT)

        # Ornstein-Uhlenbeck process drives the amplitude envelope
        self._ou = 0.0

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=CORNER_KEY)
        try:
            self.root.attributes("-transparentcolor", CORNER_KEY)
        except tk.TclError:
            pass
        self.root.attributes("-alpha", 0.93)

        sw = self.root.winfo_screenwidth()
        if sw > 3000:
            mon_offset, mon_w = sw // 2, sw // 2
        else:
            mon_offset, mon_w = 0, sw
        x = mon_offset + mon_w // 2 - WIN_W // 2
        self.root.geometry(f"{WIN_W}x{WIN_H}+{x}+24")
        self.root.lift()
        self.root.focus_force()
        try:
            # Grab all X11 keyboard events globally so Esc/Tab work without clicking
            self.root.grab_set_global()
        except tk.TclError:
            pass

        self.canvas = tk.Canvas(
            self.root, width=WIN_W, height=WIN_H, bg=CORNER_KEY, highlightthickness=0
        )
        self.canvas.pack()

        # ── Background ────────────────────────────────────────────────────────
        _draw_rounded_rect(self.canvas, 0, 0, WIN_W, WIN_H, CORNER_R, fill=BG, outline="")

        # Slightly darker toolbar area (bottom strip)
        _draw_rounded_rect(
            self.canvas, 0, WAVE_H, WIN_W, WIN_H, CORNER_R, fill=TOOLBAR_BG, outline=""
        )
        self.canvas.create_rectangle(
            0, WAVE_H, WIN_W, WAVE_H + CORNER_R, fill=TOOLBAR_BG, outline=""
        )
        # Separator line
        self.canvas.create_line(CORNER_R, WAVE_H, WIN_W - CORNER_R, WAVE_H, fill=SEP_COLOR, width=1)
        # Horizontal centre guide (very faint)
        self.canvas.create_line(
            WAVE_PAD_X, WAVE_CY, WIN_W - WAVE_PAD_X, WAVE_CY, fill="#1e1e35", width=1
        )

        # ── Waveform bars ─────────────────────────────────────────────────────
        self._bars: list[int] = []
        for i in range(BAR_COUNT):
            bx = WAVE_PAD_X + i * (BAR_W + BAR_GAP)
            bar = self.canvas.create_rectangle(
                bx,
                WAVE_CY,
                bx + BAR_W,
                WAVE_CY,
                fill=_bar_color(0.0),
                outline="",
            )
            self._bars.append(bar)

        # ── Toolbar ───────────────────────────────────────────────────────────
        tool_cy = WAVE_H + TOOL_H // 2

        # Recording dot
        self.canvas.create_oval(
            10,
            tool_cy - 4,
            18,
            tool_cy + 4,
            fill=DOT_COLOR,
            outline="",
        )

        # Mode label (white, bold, left-aligned after dot)
        self._mode_label = self.canvas.create_text(
            24,
            tool_cy,
            text=initial_mode or "—",
            font=("sans-serif", 9, "bold"),
            fill=MODE_FG,
            anchor="w",
        )

        # Shortcut badges: right side
        # Rendered right-to-left; list order = left-to-right reading order.
        # Text labels: "Stop  ", "Cancel  ", "Mode  " — badges: everything else.
        rx = WIN_W - 6
        for label in reversed(["Tab", "Mode  ", "Esc", "Cancel  ", "A+Sh+Sp", "Stop  "]):
            if label.strip() in ("Stop", "Cancel", "Mode"):  # noqa: SIM102
                rx -= 4
                self.canvas.create_text(
                    rx,
                    tool_cy,
                    text=label.strip(),
                    font=("sans-serif", 8),
                    fill=BADGE_FG,
                    anchor="e",
                )
                rx -= len(label.strip()) * 6 + 2
            else:
                bw = len(label.strip()) * 6 + 10
                rx -= bw + 2
                _draw_badge(self.canvas, rx, tool_cy, label.strip())

        self.root.bind("<Escape>", self._on_escape)
        self.root.bind("<Tab>", self._on_tab)
        # Right-click anywhere on the overlay = cancel (no focus needed)
        self.canvas.bind("<Button-3>", self._on_escape)
        self._animate()
        if not test_mode:
            self.root.after(600, self._schedule_poll)

    # ── Animation loop ────────────────────────────────────────────────────────

    def _animate(self) -> None:
        if not self._running:
            return

        # Smooth audio level
        raw = _read_level()
        norm = min(raw / LEVEL_PEAK, 1.0)
        alpha = 0.5 if norm > self._audio_level else 0.10
        self._audio_level += (norm - self._audio_level) * alpha
        display_level = max(self._audio_level, 0.14)

        # OU envelope: slow mean-reverting random walk
        self._ou += -0.07 * self._ou + random.gauss(0, 0.13)
        self._ou = max(-1.0, min(1.0, self._ou))

        # New sample: |OU| × level × slight per-frame jitter
        sample = abs(self._ou) * display_level * random.uniform(0.78, 1.22)
        self._samples.append(sample)

        # Render bars (symmetric: grows ± from WAVE_CY)
        for i, s in enumerate(self._samples):
            h = int(s * BAR_MAX_H)
            bx = WAVE_PAD_X + i * (BAR_W + BAR_GAP)
            self.canvas.coords(self._bars[i], bx, WAVE_CY - h, bx + BAR_W, WAVE_CY + h)
            self.canvas.itemconfig(self._bars[i], fill=_bar_color(s))

        self.root.after(ANIM_MS, self._animate)

    # ── Non-blocking daemon poll ──────────────────────────────────────────────

    def _schedule_poll(self) -> None:
        if not self._running:
            return

        def _do() -> None:
            resp = send_status()
            if self._running:
                self.root.after(0, lambda r=resp: self._apply_poll(r))

        threading.Thread(target=_do, daemon=True).start()
        self.root.after(POLL_MS, self._schedule_poll)

    def _apply_poll(self, resp: dict) -> None:
        if not self._running:
            return
        state = resp.get("state")
        if state == "idle" or resp.get("status") == "error":
            self._close()
            return
        mode = resp.get("mode")
        if mode:
            self.canvas.itemconfig(self._mode_label, text=mode)

    # ── Controls ──────────────────────────────────────────────────────────────

    def _on_escape(self, _event: object = None) -> None:
        send_cancel()
        self._close()

    def _on_tab(self, _event: object = None) -> None:
        def _do() -> None:
            resp = send_next_mode()
            if self._running:
                mode = resp.get("mode", "") or "—"
                self.root.after(0, lambda m=mode: self.canvas.itemconfig(self._mode_label, text=m))

        threading.Thread(target=_do, daemon=True).start()

    def _close(self) -> None:
        self._running = False
        _play(_SND_STOP)
        try:
            self.root.grab_release()
        except Exception:
            pass
        try:
            LEVEL_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    test_mode = "--test" in sys.argv or os.environ.get("VOICECLI_OVERLAY_TEST") == "1"
    if not test_mode and not SOCKET_PATH.exists():
        sys.exit(0)
    initial_mode = os.environ.get("VOICECLI_OVERLAY_MODE") or ("test" if test_mode else None)
    overlay = WaveformOverlay(initial_mode=initial_mode, test_mode=test_mode)
    if test_mode:
        overlay.root.after(8000, overlay._close)
    overlay.run()


if __name__ == "__main__":
    main()
