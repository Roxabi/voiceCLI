"""Tests for voicecli.overlay._hotkey_badge."""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Import the function under test.
# This will raise ImportError until overlay.py exposes _hotkey_badge.
# ---------------------------------------------------------------------------

from voicecli.overlay import _hotkey_badge


# ---------------------------------------------------------------------------
# Parameterized unit tests for _hotkey_badge()
# SC-7, SC-8 from the spec.
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
    ],
)
def test_hotkey_badge(hotkey: str, expected: str) -> None:
    """_hotkey_badge() converts a raw hotkey string into a compact badge label."""
    # Arrange — hotkey string provided via parametrize

    # Act
    result = _hotkey_badge(hotkey)

    # Assert
    assert result == expected, f"_hotkey_badge({hotkey!r}) → {result!r}, want {expected!r}"
