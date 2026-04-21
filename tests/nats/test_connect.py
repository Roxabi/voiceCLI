"""Tests for NATS connection via roxabi-nats SDK (issue #69).

The SDK handles nkey seed reading internally via NATS_NKEY_SEED_PATH env var.
These tests verify CLI integration with the SDK-based adapters.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestNkeyEnvVarResolution:
    """Exercise the CLI seam end-to-end via typer.testing.CliRunner.

    The SDK's nats_connect reads NATS_NKEY_SEED_PATH internally.
    These tests verify the CLI still works with env var configured.
    """

    def test_cli_connects_with_nkey_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI successfully starts adapter when NATS_NKEY_SEED_PATH is set."""
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
        # SDK's run() takes nats_url positionally; nkey seed is read from env by SDK
        assert mock_run.call_args.args == ("nats://localhost:4222",)

    def test_cli_connects_without_nkey_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLI successfully starts adapter even without NATS_NKEY_SEED_PATH (dev mode)."""
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
        assert mock_run.call_count == 1
