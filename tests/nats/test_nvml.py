"""RED-phase tests for voicecli.nats.nvml.read_vram (issue #42).

These tests FAIL until voicecli.nats.nvml is implemented.
They define the contract: read_vram() must degrade gracefully when pynvml
is absent or fails to initialise, returning (None, None) in both cases.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

from voicecli.nats.nvml import read_vram


class TestReadVram:
    def test_read_vram_returns_nulls_when_pynvml_missing(
        self, monkeypatch: Any
    ) -> None:
        """read_vram returns (None, None) when _import_pynvml returns None.

        Simulates the pynvml package being absent from the environment.
        """
        # Arrange
        monkeypatch.setattr("voicecli.nats.nvml._import_pynvml", lambda: None)

        # Act
        result = read_vram()

        # Assert
        assert result == (None, None)

    def test_read_vram_returns_nulls_on_init_error(self, monkeypatch: Any) -> None:
        """read_vram returns (None, None) when nvmlInit raises any exception.

        Simulates a driver not found or incompatible NVML scenario.
        """
        # Arrange
        def _raise_on_init() -> None:
            raise RuntimeError("NVML driver not loaded")

        fake_nvml = ModuleType("pynvml")
        fake_nvml.nvmlInit = _raise_on_init  # type: ignore[attr-defined]

        monkeypatch.setattr("voicecli.nats.nvml._import_pynvml", lambda: fake_nvml)

        # Act
        result = read_vram()

        # Assert
        assert result == (None, None)
