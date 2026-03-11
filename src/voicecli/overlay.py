"""Floating waveform overlay shown during STT recording.

Run as: python -m voicecli.overlay
Closes automatically when the daemon is no longer in 'recording' state.
Press ESC to cancel the recording.
"""

from __future__ import annotations

import math
import os
import random
import sys
import tkinter as tk

from voicecli.stt_client import SOCKET_PATH, send_cancel, send_status

# ── Layout constants ───────────────────────────────────────────────────────────
BAR_COUNT = 24
BAR_W = 7
BAR_GAP = 3
PAD_X = 16
PAD_Y = 12
BAR_MAX_H = 28  # max half-height in px
BAR_MIN_H = 3  # min half-height in px
ICON_W = 34  # space reserved for 🎤

POLL_MS = 150  # daemon status poll interval
ANIM_MS = 70  # frame interval (~14 fps)

BG = "#1a1a2e"
BAR_COLOR_LOW = "#4a4a8a"
BAR_COLOR_HIGH = "#e94560"
TEXT_COLOR = "#ffffff"


def _bar_color(frac: float) -> str:
    """Interpolate from BAR_COLOR_LOW to BAR_COLOR_HIGH."""
    lr, lg, lb = 0x4A, 0x4A, 0x8A
    hr, hg, hb = 0xE9, 0x45, 0x60
    r = int(lr + (hr - lr) * frac)
    g = int(lg + (hg - lg) * frac)
    b = int(lb + (hb - lb) * frac)
    return f"#{r:02x}{g:02x}{b:02x}"


class WaveformOverlay:
    def __init__(self) -> None:
        self._running = True
        self._heights = [float(BAR_MIN_H)] * BAR_COUNT
        self._targets = [float(BAR_MIN_H)] * BAR_COUNT
        self._phase = 0.0  # drives the voice-like undulation

        win_w = PAD_X * 2 + ICON_W + BAR_COUNT * (BAR_W + BAR_GAP)
        win_h = BAR_MAX_H * 2 + PAD_Y * 2

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)
        self.root.attributes("-alpha", 0.93)

        # Position: top-center of primary monitor (avoids taskbar at bottom)
        # Halve width on wide setups (>3000px = likely dual monitor side-by-side)
        sw = self.root.winfo_screenwidth()
        mon_w = sw // 2 if sw > 3000 else sw
        x = mon_w // 2 - win_w // 2
        y = 24
        self.root.geometry(f"{win_w}x{win_h}+{x}+{y}")

        self.canvas = tk.Canvas(self.root, width=win_w, height=win_h, bg=BG, highlightthickness=0)
        self.canvas.pack()

        self._cy = win_h // 2  # vertical center

        # Mic icon
        self.canvas.create_text(
            PAD_X + ICON_W // 2,
            self._cy,
            text="🎤",
            font=("Segoe UI Emoji", 15),
            fill=TEXT_COLOR,
            anchor="center",
        )

        # Waveform bars
        self._bars: list[int] = []
        x0 = PAD_X + ICON_W
        for i in range(BAR_COUNT):
            x = x0 + i * (BAR_W + BAR_GAP)
            bar = self.canvas.create_rectangle(
                x,
                self._cy - BAR_MIN_H,
                x + BAR_W,
                self._cy + BAR_MIN_H,
                fill=BAR_COLOR_LOW,
                outline="",
            )
            self._bars.append(bar)

        self.root.bind("<Escape>", self._on_escape)

        self._animate()
        self.root.after(600, self._poll)  # grace period for Python startup

    def _on_escape(self, _event: object = None) -> None:
        send_cancel()
        self._close()

    def _animate(self) -> None:
        if not self._running:
            return

        self._phase += 0.35
        x0 = PAD_X + ICON_W

        # Generate new targets: sine envelope + noise gives a voice-like shape
        new_targets = []
        for i in range(BAR_COUNT):
            wave = 0.55 + 0.45 * math.sin(self._phase + i * 0.6)
            noise = random.uniform(0.6, 1.0)
            h = BAR_MIN_H + (BAR_MAX_H - BAR_MIN_H) * wave * noise
            new_targets.append(h)
        self._targets = new_targets

        for i, bar in enumerate(self._bars):
            # Smooth toward target
            self._heights[i] += (self._targets[i] - self._heights[i]) * 0.35
            h = self._heights[i]
            x = x0 + i * (BAR_W + BAR_GAP)
            self.canvas.coords(bar, x, self._cy - h, x + BAR_W, self._cy + h)
            self.canvas.itemconfig(bar, fill=_bar_color(h / BAR_MAX_H))

        self.root.after(ANIM_MS, self._animate)

    def _poll(self) -> None:
        if not self._running:
            return
        resp = send_status()
        state = resp.get("state")
        if state == "idle" or resp.get("status") == "error":
            self._close()
            return
        # Stay open during recording, queued, and transcribing
        self.root.after(POLL_MS, self._poll)

    def _close(self) -> None:
        self._running = False
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    test_mode = "--test" in sys.argv or os.environ.get("VOICECLI_OVERLAY_TEST") == "1"
    if not test_mode and not SOCKET_PATH.exists():
        sys.exit(0)  # daemon not running — nothing to show
    overlay = WaveformOverlay()
    if test_mode:
        overlay.root.after(5000, overlay._close)
    overlay.run()


if __name__ == "__main__":
    main()
