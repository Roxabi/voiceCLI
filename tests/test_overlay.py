"""Tests for voicecli.ui.overlay_draw.hotkey_badge and voicecli.ui.overlay._resolve_test_mode."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

# Mock GTK dependencies before importing overlay
sys.modules["cairo"] = MagicMock()
sys.modules["gi"] = MagicMock()
sys.modules["gi.repository"] = MagicMock()
sys.modules["gi.repository.Gdk"] = MagicMock()
sys.modules["gi.repository.GLib"] = MagicMock()
sys.modules["gi.repository.Gtk"] = MagicMock()
sys.modules["gi.repository.GtkLayerShell"] = MagicMock()

from voicecli.ui.overlay import _resolve_test_mode  # noqa: E402
from voicecli.ui.overlay_draw import hotkey_badge as _hotkey_badge  # noqa: E402


# ---------------------------------------------------------------------------
# Parameterized unit tests for _hotkey_badge()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hotkey,expected",
    [
        # ctrl alone (no other modifier) → full "Ctrl"
        ("ctrl+space", "Ctrl+Sp"),
        # alt + shift + key
        ("alt+shift+esc", "A+Sh+Esc"),
        ("alt+shift+tab", "A+Sh+Tab"),
        # ctrl combined with shift → abbreviated "C"
        ("ctrl+shift+d", "C+Sh+D"),
        # alt alone + function/named key
        ("alt+f4", "A+F4"),
        # unknown modifier passes through gracefully (capitalize first letter)
        ("super+space", "Super+Sp"),
        # single-part (no plus sign) — bare key from misconfigured env var
        ("space", "Sp"),
        # mixed-case input — .lower() normalizes before lookup
        ("Ctrl+Space", "Ctrl+Sp"),
    ],
)
def test_hotkey_badge(hotkey: str, expected: str) -> None:
    """_hotkey_badge() converts a raw hotkey string into a compact badge label."""
    result = _hotkey_badge(hotkey)
    assert result == expected, f"_hotkey_badge({hotkey!r}) → {result!r}, want {expected!r}"


# ---------------------------------------------------------------------------
# Tests for _resolve_test_mode() — verifies coerce_bool_env migration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_val,expected",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("ON", True),
        ("0", False),
        ("false", False),
        ("", False),
    ],
)
def test_resolve_test_mode_env_var(monkeypatch, env_val: str, expected: bool) -> None:
    """_resolve_test_mode() uses coerce_bool_env for VOICECLI_OVERLAY_TEST."""
    monkeypatch.delenv("VOICECLI_OVERLAY_TEST", raising=False)
    monkeypatch.setenv("VOICECLI_OVERLAY_TEST", env_val)
    # Clear --test from sys.argv for this test
    original_argv = sys.argv.copy()
    sys.argv = [sys.argv[0]]  # Only script name, no --test
    try:
        assert _resolve_test_mode() is expected
    finally:
        sys.argv = original_argv


def test_resolve_test_mode_cli_flag(monkeypatch) -> None:
    """_resolve_test_mode() returns True when --test is in sys.argv."""
    monkeypatch.delenv("VOICECLI_OVERLAY_TEST", raising=False)
    original_argv = sys.argv.copy()
    sys.argv = [sys.argv[0], "--test"]
    try:
        assert _resolve_test_mode() is True
    finally:
        sys.argv = original_argv
