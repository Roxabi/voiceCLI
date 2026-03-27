"""Floating waveform overlay shown during STT recording.

Run as: python -m voicecli.overlay
Closes automatically when the daemon is no longer in 'recording' state.

Design: dark rounded panel with symmetric waveform bars (grow +/- from centre)
and a bottom toolbar showing mode name + keyboard shortcuts.

Rendering: GTK3 + gtk-layer-shell (Wayland-native) with Cairo drawing.
Falls back to GTK3 without layer-shell (X11/XWayland) if gtk-layer-shell
is not available.
"""

from __future__ import annotations

import math
import os
import random
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell  # noqa: E402

    HAS_LAYER_SHELL = True
except (ValueError, ImportError):
    HAS_LAYER_SHELL = False

from voicecli.stt_client import SOCKET_PATH, send_status  # noqa: E402
from voicecli.stt_daemon import LEVEL_FILE  # noqa: E402

_ASSETS = Path(__file__).parent / "assets"

_ABBREV = {
    "alt": "A",
    "shift": "Sh",
    "space": "Sp",
    "esc": "Esc",
    "tab": "Tab",
}


def _hotkey_badge(hotkey_str: str) -> str:
    """Convert config hotkey string to short display badge text."""
    parts = hotkey_str.lower().split("+")
    has_other_modifier = any(p in ("alt", "shift") for p in parts)
    result = []
    for p in parts:
        if p == "ctrl":
            result.append("C" if has_other_modifier else "Ctrl")
        elif p in _ABBREV:
            result.append(_ABBREV[p])
        else:
            result.append(p.capitalize())
    return "+".join(result)


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
BAR_MAX_H = 22  # max half-height of a bar (grows +/- from WAVE_CY)

POLL_MS = 200
ANIM_MS = 50  # ~20 fps
WATCHDOG_S = 15  # auto-close after N seconds without a valid poll response

LEVEL_PEAK = 0.06

# ── Colors (RGBA tuples for Cairo) ────────────────────────────────────────────
BG = (0x14 / 255, 0x14 / 255, 0x20 / 255, 0.93)
TOOLBAR_BG = (0x0D / 255, 0x0D / 255, 0x18 / 255, 0.93)
SEP_COLOR = (0x25 / 255, 0x25 / 255, 0x40 / 255, 1.0)
GUIDE_COLOR = (0x1E / 255, 0x1E / 255, 0x35 / 255, 1.0)
BAR_DIM = (0x28 / 255, 0x28 / 255, 0x48 / 255)
BAR_BRIGHT = (0xDD / 255, 0xDD / 255, 0xFF / 255)
BADGE_BG = (0x25 / 255, 0x25 / 255, 0x42 / 255, 1.0)
BADGE_BORDER = (0x44 / 255, 0x44 / 255, 0x6A / 255, 1.0)
BADGE_FG = (0x99 / 255, 0x99 / 255, 0xBB / 255, 1.0)
MODE_FG = (1.0, 1.0, 1.0, 1.0)
DOT_COLOR = (0xE9 / 255, 0x45 / 255, 0x60 / 255, 1.0)


def _bar_color(frac: float) -> tuple[float, float, float]:
    """Interpolate BAR_DIM -> BAR_BRIGHT by height fraction."""
    t = max(0.0, min(1.0, frac))
    return (
        BAR_DIM[0] + (BAR_BRIGHT[0] - BAR_DIM[0]) * t,
        BAR_DIM[1] + (BAR_BRIGHT[1] - BAR_DIM[1]) * t,
        BAR_DIM[2] + (BAR_BRIGHT[2] - BAR_DIM[2]) * t,
    )


def _read_level() -> float:
    try:
        return float(LEVEL_FILE.read_text().strip())
    except Exception:
        return 0.0


def _rounded_rect(cr: object, x: float, y: float, w: float, h: float, r: float) -> None:
    """Add a rounded rectangle sub-path to the Cairo context."""
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class WaveformOverlay:
    def __init__(self, initial_mode: str | None = None, test_mode: bool = False) -> None:
        self._running = True
        self._test_mode = test_mode
        self._audio_level = 0.0
        self._cached_level = 0.0
        self._poll_in_flight = False
        self._last_good_poll = time.monotonic()
        self._mode_text = initial_mode or "—"

        # Ring buffer of amplitudes (one per bar, scrolls left each frame)
        self._samples: deque[float] = deque([0.0] * BAR_COUNT, maxlen=BAR_COUNT)

        # Ornstein-Uhlenbeck process drives the amplitude envelope
        self._ou = 0.0

        # Hotkey badges
        self._hk_toggle = _hotkey_badge(
            os.environ.get("VOICECLI_OVERLAY_HOTKEY_TOGGLE") or "ctrl+space"
        )
        self._hk_cancel = _hotkey_badge(
            os.environ.get("VOICECLI_OVERLAY_HOTKEY_CANCEL") or "alt+shift+esc"
        )
        self._hk_mode = _hotkey_badge(
            os.environ.get("VOICECLI_OVERLAY_HOTKEY_MODE") or "alt+shift+tab"
        )

        # ── Window setup ──────────────────────────────────────────────────────
        self.window = Gtk.Window()
        self.window.set_default_size(WIN_W, WIN_H)
        self.window.set_resizable(False)
        self.window.set_decorated(False)

        # RGBA transparency
        screen = self.window.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.window.set_visual(visual)
        self.window.set_app_paintable(True)

        if HAS_LAYER_SHELL:
            # Wayland-native overlay via layer-shell
            GtkLayerShell.init_for_window(self.window)
            GtkLayerShell.set_layer(self.window, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.TOP, True)
            GtkLayerShell.set_margin(self.window, GtkLayerShell.Edge.TOP, 24)
            GtkLayerShell.set_keyboard_mode(self.window, GtkLayerShell.KeyboardMode.NONE)
        else:
            # X11/XWayland fallback
            self.window.set_keep_above(True)
            self.window.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
            self.window.set_accept_focus(False)
            # Centre horizontally, near top
            screen_w = screen.get_width()
            self.window.move(screen_w // 2 - WIN_W // 2, 24)

        # Drawing area
        self._drawing_area = Gtk.DrawingArea()
        self._drawing_area.set_size_request(WIN_W, WIN_H)
        self._drawing_area.connect("draw", self._on_draw)
        self.window.add(self._drawing_area)

        self.window.connect("destroy", self._on_destroy)

        self.window.show_all()

        # ── Start loops ───────────────────────────────────────────────────────
        self._start_level_reader()
        GLib.timeout_add(ANIM_MS, self._animate)
        if not test_mode:
            GLib.timeout_add(600, self._schedule_poll)

    # ── Cairo drawing ─────────────────────────────────────────────────────────

    def _on_draw(self, widget: Gtk.DrawingArea, cr: object) -> bool:
        # Clear to fully transparent
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        # ── Main background (rounded rect) ────────────────────────────────────
        _rounded_rect(cr, 0, 0, WIN_W, WIN_H, CORNER_R)
        cr.set_source_rgba(*BG)
        cr.fill()

        # ── Toolbar background (bottom strip) ─────────────────────────────────
        _rounded_rect(cr, 0, WAVE_H, WIN_W, TOOL_H, CORNER_R)
        cr.set_source_rgba(*TOOLBAR_BG)
        cr.fill()
        # Fill the gap between waveform and toolbar rounded corners
        cr.rectangle(0, WAVE_H, WIN_W, CORNER_R)
        cr.set_source_rgba(*TOOLBAR_BG)
        cr.fill()

        # ── Separator line ────────────────────────────────────────────────────
        cr.set_source_rgba(*SEP_COLOR)
        cr.set_line_width(1)
        cr.move_to(CORNER_R, WAVE_H + 0.5)
        cr.line_to(WIN_W - CORNER_R, WAVE_H + 0.5)
        cr.stroke()

        # ── Centre guide ──────────────────────────────────────────────────────
        cr.set_source_rgba(*GUIDE_COLOR)
        cr.move_to(WAVE_PAD_X, WAVE_CY + 0.5)
        cr.line_to(WIN_W - WAVE_PAD_X, WAVE_CY + 0.5)
        cr.stroke()

        # ── Waveform bars ─────────────────────────────────────────────────────
        for i, s in enumerate(self._samples):
            h = int(s * BAR_MAX_H)
            bx = WAVE_PAD_X + i * (BAR_W + BAR_GAP)
            r, g, b = _bar_color(s)
            cr.set_source_rgba(r, g, b, 1.0)
            if h > 0:
                cr.rectangle(bx, WAVE_CY - h, BAR_W, h * 2)
                cr.fill()

        # ── Toolbar content ───────────────────────────────────────────────────
        tool_cy = WAVE_H + TOOL_H / 2

        # Recording dot
        cr.set_source_rgba(*DOT_COLOR)
        cr.arc(14, tool_cy, 4, 0, 2 * math.pi)
        cr.fill()

        # Mode label
        cr.set_source_rgba(*MODE_FG)
        cr.select_font_face("sans-serif", 0, 1)  # NORMAL, BOLD
        cr.set_font_size(11)
        cr.move_to(24, tool_cy + 4)
        cr.show_text(self._mode_text)

        # Shortcut badges (right-aligned)
        cr.set_font_size(9)
        rx = WIN_W - 6
        for label, badge_text in reversed(
            [
                ("Stop", self._hk_toggle),
                ("Cancel", self._hk_cancel),
                ("Mode", self._hk_mode),
            ]
        ):
            # Draw badge
            rx = self._draw_badge(cr, rx, tool_cy, badge_text)
            rx -= 4
            # Draw label text
            cr.set_source_rgba(*BADGE_FG)
            cr.set_font_size(9)
            extents = cr.text_extents(label)
            rx -= extents.width
            cr.move_to(rx, tool_cy + 3.5)
            cr.show_text(label)
            rx -= 8

        return True

    def _draw_badge(self, cr: object, rx: float, cy: float, label: str) -> float:
        """Draw a keyboard-key badge right-aligned at rx. Returns new rx."""
        cr.set_font_size(8)
        extents = cr.text_extents(label)
        pad = 5
        bw = extents.width + pad * 2
        bh = 14
        bx = rx - bw
        by = cy - bh / 2
        br = 3

        # Badge background
        _rounded_rect(cr, bx, by, bw, bh, br)
        cr.set_source_rgba(*BADGE_BG)
        cr.fill_preserve()
        cr.set_source_rgba(*BADGE_BORDER)
        cr.set_line_width(1)
        cr.stroke()

        # Badge text
        cr.set_source_rgba(*BADGE_FG)
        cr.move_to(bx + pad, cy + 3)
        cr.show_text(label)

        return bx - 2

    # ── Background level reader ───────────────────────────────────────────────

    def _start_level_reader(self) -> None:
        def _loop() -> None:
            while self._running:
                self._cached_level = _read_level()
                time.sleep(ANIM_MS / 1000.0)

        threading.Thread(target=_loop, daemon=True, name="level-reader").start()

    # ── Animation loop ────────────────────────────────────────────────────────

    def _animate(self) -> bool:
        if not self._running:
            return False  # stop the GLib timer

        # Smooth audio level
        raw = self._cached_level
        norm = min(raw / LEVEL_PEAK, 1.0)
        alpha = 0.5 if norm > self._audio_level else 0.10
        self._audio_level += (norm - self._audio_level) * alpha
        display_level = max(self._audio_level, 0.14)

        # OU envelope: slow mean-reverting random walk
        self._ou += -0.07 * self._ou + random.gauss(0, 0.13)
        self._ou = max(-1.0, min(1.0, self._ou))

        # New sample
        sample = abs(self._ou) * display_level * random.uniform(0.78, 1.22)
        self._samples.append(sample)

        # Trigger redraw
        self._drawing_area.queue_draw()
        return True  # keep timer running

    # ── Non-blocking daemon poll ──────────────────────────────────────────────

    def _schedule_poll(self) -> bool:
        if not self._running:
            return False

        if not self._poll_in_flight and time.monotonic() - self._last_good_poll > WATCHDOG_S:
            self._close()
            return False

        if not self._poll_in_flight:
            self._poll_in_flight = True

            def _do() -> None:
                try:
                    resp = send_status()
                finally:
                    self._poll_in_flight = False
                if self._running:
                    GLib.idle_add(self._apply_poll, resp)

            threading.Thread(target=_do, daemon=True).start()

        return True  # keep timer running

    def _apply_poll(self, resp: dict) -> bool:
        if not self._running:
            return False
        state = resp.get("state")
        if state != "recording" or resp.get("status") == "error":
            self._close()
            return False
        self._last_good_poll = time.monotonic()
        mode = resp.get("mode")
        if mode:
            self._mode_text = mode
        return False  # one-shot idle callback

    # ── Controls ──────────────────────────────────────────────────────────────

    def _on_destroy(self, _widget: object) -> None:
        self._running = False

    def _close(self) -> None:
        if not self._running:
            return
        self._running = False
        _play(_SND_STOP)
        try:
            LEVEL_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            self.window.destroy()
        except Exception:
            pass

    def run(self) -> None:
        Gtk.main()


def main() -> None:
    test_mode = "--test" in sys.argv or os.environ.get("VOICECLI_OVERLAY_TEST") == "1"
    if not test_mode and not SOCKET_PATH.exists():
        sys.exit(0)
    initial_mode = os.environ.get("VOICECLI_OVERLAY_MODE") or ("test" if test_mode else None)
    overlay = WaveformOverlay(initial_mode=initial_mode, test_mode=test_mode)
    if test_mode:
        GLib.timeout_add(8000, overlay._close)
    overlay.run()


if __name__ == "__main__":
    main()
