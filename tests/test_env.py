"""Unit tests for env.coerce_bool_env."""

from __future__ import annotations

import pytest
from voicecli.core.env import coerce_bool_env


class TestCoerceBoolEnv:
    """Unit tests for env.coerce_bool_env."""

    @pytest.mark.parametrize(
        "value",
        [
            "1",
            "true",
            "TRUE",
            "True",
            "yes",
            "YES",
            "on",
            "ON",
        ],
    )
    def test_true_values(self, monkeypatch, value: str) -> None:
        """Accepted values return True."""
        # Arrange
        monkeypatch.setenv("TEST_VAR", value)

        # Act & Assert
        assert coerce_bool_env("TEST_VAR") is True

    @pytest.mark.parametrize(
        "value",
        [
            "0",
            "false",
            "no",
            "off",
            "",
            "random",
            "2",
        ],
    )
    def test_false_values(self, monkeypatch, value: str) -> None:
        """Non-accepted values return False."""
        # Arrange
        monkeypatch.setenv("TEST_VAR", value)

        # Act & Assert
        assert coerce_bool_env("TEST_VAR") is False

    def test_unset_returns_default(self, monkeypatch) -> None:
        """Unset variable returns default."""
        # Arrange
        monkeypatch.delenv("TEST_VAR", raising=False)

        # Act & Assert
        assert coerce_bool_env("TEST_VAR") is False
        assert coerce_bool_env("TEST_VAR", default=True) is True

    @pytest.mark.parametrize(
        "value",
        [
            " true ",
            " 1",
            "1 ",
            "  true  ",
        ],
    )
    def test_whitespace_values_return_false(self, monkeypatch, value: str) -> None:
        """Whitespace-padded values return False (no .strip())."""
        # Arrange
        monkeypatch.setenv("TEST_VAR", value)

        # Act & Assert
        assert coerce_bool_env("TEST_VAR") is False
