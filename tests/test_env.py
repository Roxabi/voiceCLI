"""RED-phase tests for coerce_bool_env (#56).

These tests verify the expected behaviour once coerce_bool_env is
implemented in env.py. They are intentionally written to FAIL against
the current unmodified codebase (RED phase).
"""

from __future__ import annotations

import pytest


class TestCoerceBoolEnv:
    """Unit tests for env.coerce_bool_env."""

    def test_true_values(self, monkeypatch):
        """Accepted values return True."""
        from voicecli.env import coerce_bool_env

        # Arrange
        for val in ("1", "true", "TRUE", "True", "yes", "YES", "on", "ON"):
            monkeypatch.setenv("TEST_VAR", val)

            # Act & Assert
            assert coerce_bool_env("TEST_VAR") is True

    def test_false_values(self, monkeypatch):
        """Non-accepted values return False."""
        from voicecli.env import coerce_bool_env

        # Arrange
        for val in ("0", "false", "no", "off", "", "random", "2"):
            monkeypatch.setenv("TEST_VAR", val)

            # Act & Assert
            assert coerce_bool_env("TEST_VAR") is False

    def test_unset_returns_default(self, monkeypatch):
        """Unset variable returns default."""
        from voicecli.env import coerce_bool_env

        # Arrange
        monkeypatch.delenv("TEST_VAR", raising=False)

        # Act & Assert
        assert coerce_bool_env("TEST_VAR") is False
        assert coerce_bool_env("TEST_VAR", default=True) is True
