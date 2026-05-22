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

import os
import random
import sys
import threading
import time
from collections import deque

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

from voicecli.core.env import coerce_bool_env  # noqa: E402
from voicecli.ui.overlay_audio import LEVEL_PEAK, SND_STOP, play, read_level  # noqa: E402
from voicecli.ui.overlay_draw import (  # noqa: E402
    ANIM_MS,
    BAR_COUNT,
    WATCHDOG_S,
    WIN_H,
    WIN_W,
    draw_frame,
    hotkey_badge,
)
from voicecli.ui.stt_client import SOCKET_PATH, send_status  # noqa: E402
from voicecli.runtime.stt_daemon import LEVEL_FILE  # noqa: E402


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
        self._hk_toggle = hotkey_badge(
            os.environ.get("VOICECLI_OVERLAY_HOTKEY_TOGGLE") or "ctrl+space"
        )
        self._hk_cancel = hotkey_badge(
            os.environ.get("VOICECLI_OVERLAY_HOTKEY_CANCEL") or "alt+shift+esc"
        )
        self._hk_mode = hotkey_badge(
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
        draw_frame(
            cr,
            list(self._samples),
            self._mode_text,
            self._hk_toggle,
            self._hk_cancel,
            self._hk_mode,
        )
        return True

    # ── Background level reader ───────────────────────────────────────────────

    def _start_level_reader(self) -> None:
        def _loop() -> None:
            while self._running:
                self._cached_level = read_level()
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
        play(SND_STOP)
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


def _resolve_test_mode() -> bool:
    """Resolve test mode from CLI args or env var."""
    return "--test" in sys.argv or coerce_bool_env("VOICECLI_OVERLAY_TEST")


def main() -> None:
    test_mode = _resolve_test_mode()
    if not test_mode and not SOCKET_PATH.exists():
        sys.exit(0)
    initial_mode = os.environ.get("VOICECLI_OVERLAY_MODE") or ("test" if test_mode else None)
    overlay = WaveformOverlay(initial_mode=initial_mode, test_mode=test_mode)
    if test_mode:
        GLib.timeout_add(8000, overlay._close)
    overlay.run()


if __name__ == "__main__":
    main()
