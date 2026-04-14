"""RED-phase tests for voicecli.nats.connect._read_nkey_seed (issue #42).

These tests FAIL until the voicecli.nats package is implemented.
They define the contract for _read_nkey_seed(path: Path) -> KeyPair-like object.

Note: unlike the lyra port which reads from an env var, voicecli's version
accepts an explicit Path so it integrates cleanly with the CLI option pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voicecli.nats.connect import _read_nkey_seed


class TestNkeyEnvVarResolution:
    """Exercise the real CLI seam end-to-end via typer.testing.CliRunner.

    These tests drive `voicecli nats-serve tts` and patch `TtsNatsAdapter.run`
    to capture the kwargs the CLI passes in. A regression where the CLI stops
    reading NATS_NKEY_SEED_PATH, or forgets to forward it to adapter.run(),
    will fail these tests.
    """

    def test_cli_forwards_nkey_seed_path_from_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import AsyncMock, patch

        from typer.testing import CliRunner

        from voicecli.cli import app

        seed_file = tmp_path / "test.seed"
        seed_file.write_text("SUAXXX123FAKESEEDFORTEST")
        seed_file.chmod(0o600)
        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", str(seed_file))

        with (
            patch("voicecli.cli._probe_socket_daemon", return_value="absent"),
            patch(
                "voicecli.nats.tts_adapter.TtsNatsAdapter.run",
                new_callable=AsyncMock,
            ) as mock_run,
            patch("voicecli.nats.tts_adapter._engine_available", return_value=True),
        ):
            result = CliRunner().invoke(app, ["nats-serve", "tts", "--engine", "qwen-fast"])

        assert result.exit_code == 0, result.output
        assert mock_run.call_count == 1
        assert mock_run.call_args.kwargs["nkey_seed_path"] == seed_file

    def test_cli_passes_none_when_env_var_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from unittest.mock import AsyncMock, patch

        from typer.testing import CliRunner

        from voicecli.cli import app

        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
        monkeypatch.delenv("NATS_NKEY_SEED_PATH", raising=False)

        with (
            patch("voicecli.cli._probe_socket_daemon", return_value="absent"),
            patch(
                "voicecli.nats.tts_adapter.TtsNatsAdapter.run",
                new_callable=AsyncMock,
            ) as mock_run,
            patch("voicecli.nats.tts_adapter._engine_available", return_value=True),
        ):
            result = CliRunner().invoke(app, ["nats-serve", "tts", "--engine", "qwen-fast"])

        assert result.exit_code == 0, result.output
        assert mock_run.call_args.kwargs["nkey_seed_path"] is None

    def test_cli_expands_tilde_in_seed_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import AsyncMock, patch

        from typer.testing import CliRunner

        from voicecli.cli import app

        seed_file = tmp_path / "tilde.seed"
        seed_file.write_text("SUAXXX123FAKESEEDFORTEST")
        seed_file.chmod(0o600)
        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", "~/tilde.seed")

        with (
            patch("voicecli.cli._probe_socket_daemon", return_value="absent"),
            patch(
                "voicecli.nats.tts_adapter.TtsNatsAdapter.run",
                new_callable=AsyncMock,
            ) as mock_run,
            patch("voicecli.nats.tts_adapter._engine_available", return_value=True),
        ):
            result = CliRunner().invoke(app, ["nats-serve", "tts", "--engine", "qwen-fast"])

        assert result.exit_code == 0, result.output
        assert mock_run.call_args.kwargs["nkey_seed_path"] == seed_file


class TestReadNkeySeed:
    def test_nkey_seed_permission_check(self, tmp_path: Path) -> None:
        """_read_nkey_seed raises PermissionError for group/world-readable files.

        A seed file at mode 0644 (group+world read) must be rejected with a
        PermissionError whose message mentions "0600" to guide the user toward
        the correct permission.
        """
        # Arrange
        seed_file = tmp_path / "nkey.seed"
        seed_file.write_text("SUAXXX123")
        seed_file.chmod(0o644)

        # Act + Assert
        with pytest.raises(PermissionError, match="0600"):
            _read_nkey_seed(seed_file)

    def test_nkey_seed_loads_with_correct_perms(self, tmp_path: Path) -> None:
        """_read_nkey_seed returns a non-None KeyPair-like object for a 0600 seed file.

        A seed file starting with 'SUA' at mode 0600 is a valid nkey user seed.
        The returned object must be non-None (KeyPair or equivalent).
        """
        # Arrange
        seed_file = tmp_path / "nkey.seed"
        seed_file.write_text("SUAXXX123FAKESEEDFORTEST")
        seed_file.chmod(0o600)

        # Act
        result = _read_nkey_seed(seed_file)

        # Assert
        assert result is not None
