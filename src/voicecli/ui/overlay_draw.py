"""Cairo drawing helpers for the waveform overlay.

Pure geometry + colour functions — no GTK state, safe to import in tests.
"""

from __future__ import annotations

import math

import cairo

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

# ── Hotkey abbreviation table ─────────────────────────────────────────────────
_ABBREV = {
    "alt": "A",
    "shift": "Sh",
    "space": "Sp",
    "esc": "Esc",
    "tab": "Tab",
}


def hotkey_badge(hotkey_str: str) -> str:
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


def bar_color(frac: float) -> tuple[float, float, float]:
    """Interpolate BAR_DIM -> BAR_BRIGHT by height fraction."""
    t = max(0.0, min(1.0, frac))
    return (
        BAR_DIM[0] + (BAR_BRIGHT[0] - BAR_DIM[0]) * t,
        BAR_DIM[1] + (BAR_BRIGHT[1] - BAR_DIM[1]) * t,
        BAR_DIM[2] + (BAR_BRIGHT[2] - BAR_DIM[2]) * t,
    )


def rounded_rect(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
    """Add a rounded rectangle sub-path to the Cairo context."""
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def draw_badge(cr: cairo.Context, rx: float, cy: float, label: str) -> float:
    """Draw a keyboard-key badge right-aligned at rx. Returns new rx."""
    cr.set_font_size(8)
    extents = cr.text_extents(label)
    pad = 5
    bw = extents.width + pad * 2
    bh = 14
    bx = rx - bw
    by = cy - bh / 2
    br = 3

    rounded_rect(cr, bx, by, bw, bh, br)
    cr.set_source_rgba(*BADGE_BG)
    cr.fill_preserve()
    cr.set_source_rgba(*BADGE_BORDER)
    cr.set_line_width(1)
    cr.stroke()

    cr.set_source_rgba(*BADGE_FG)
    cr.move_to(bx + pad, cy + 3)
    cr.show_text(label)

    return bx - 2


def draw_frame(
    cr: cairo.Context,
    samples: list[float],
    mode_text: str,
    hk_toggle: str,
    hk_cancel: str,
    hk_mode: str,
) -> None:
    """Render a complete overlay frame onto the Cairo context."""
    # Clear to fully transparent
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)

    # ── Main background ───────────────────────────────────────────────────────
    rounded_rect(cr, 0, 0, WIN_W, WIN_H, CORNER_R)
    cr.set_source_rgba(*BG)
    cr.fill()

    # ── Toolbar background ────────────────────────────────────────────────────
    rounded_rect(cr, 0, WAVE_H, WIN_W, TOOL_H, CORNER_R)
    cr.set_source_rgba(*TOOLBAR_BG)
    cr.fill()
    # Fill the gap between waveform and toolbar rounded corners
    cr.rectangle(0, WAVE_H, WIN_W, CORNER_R)
    cr.set_source_rgba(*TOOLBAR_BG)
    cr.fill()

    # ── Separator line ────────────────────────────────────────────────────────
    cr.set_source_rgba(*SEP_COLOR)
    cr.set_line_width(1)
    cr.move_to(CORNER_R, WAVE_H + 0.5)
    cr.line_to(WIN_W - CORNER_R, WAVE_H + 0.5)
    cr.stroke()

    # ── Centre guide ──────────────────────────────────────────────────────────
    cr.set_source_rgba(*GUIDE_COLOR)
    cr.move_to(WAVE_PAD_X, WAVE_CY + 0.5)
    cr.line_to(WIN_W - WAVE_PAD_X, WAVE_CY + 0.5)
    cr.stroke()

    # ── Waveform bars ─────────────────────────────────────────────────────────
    for i, s in enumerate(samples):
        h = int(s * BAR_MAX_H)
        bx = WAVE_PAD_X + i * (BAR_W + BAR_GAP)
        r, g, b = bar_color(s)
        cr.set_source_rgba(r, g, b, 1.0)
        if h > 0:
            cr.rectangle(bx, WAVE_CY - h, BAR_W, h * 2)
            cr.fill()

    # ── Toolbar content ───────────────────────────────────────────────────────
    tool_cy = WAVE_H + TOOL_H / 2

    cr.set_source_rgba(*DOT_COLOR)
    cr.arc(14, tool_cy, 4, 0, 2 * math.pi)
    cr.fill()

    cr.set_source_rgba(*MODE_FG)
    cr.select_font_face("sans-serif", cairo.FontSlant.NORMAL, cairo.FontWeight.BOLD)
    cr.set_font_size(11)
    cr.move_to(24, tool_cy + 4)
    cr.show_text(mode_text)

    cr.set_font_size(9)
    rx = WIN_W - 6
    for label, badge_text in reversed(
        [("Stop", hk_toggle), ("Cancel", hk_cancel), ("Mode", hk_mode)]
    ):
        rx = draw_badge(cr, rx, tool_cy, badge_text)
        rx -= 4
        cr.set_source_rgba(*BADGE_FG)
        cr.set_font_size(9)
        extents = cr.text_extents(label)
        rx -= extents.width
        cr.move_to(rx, tool_cy + 3.5)
        cr.show_text(label)
        rx -= 8
