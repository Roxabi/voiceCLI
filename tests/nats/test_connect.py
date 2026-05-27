"""Tests for NATS connection via roxabi-nats SDK (issue #69).

The SDK handles nkey seed reading internally via NATS_NKEY_SEED_PATH env var.
These tests verify CLI integration with the SDK-based adapters.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestSubAppImports:
    """Smoke-test that CLI sub-app commands are wired and invocable."""

    def test_samples_app_invocable(self) -> None:
        """samples sub-app help exits cleanly."""
        from typer.testing import CliRunner

        from voicecli.cli import app

        result = CliRunner().invoke(app, ["samples", "--help"])
        assert result.exit_code == 0, result.output

    def test_dictate_app_invocable(self) -> None:
        """dictate sub-app help exits cleanly."""
        from typer.testing import CliRunner

        from voicecli.cli import app

        result = CliRunner().invoke(app, ["dictate", "--help"])
        assert result.exit_code == 0, result.output

    def test_nats_serve_app_invocable(self) -> None:
        """nats-serve sub-app help exits cleanly."""
        from typer.testing import CliRunner

        from voicecli.cli import app

        result = CliRunner().invoke(app, ["nats-serve", "--help"])
        assert result.exit_code == 0, result.output


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
            patch("voicecli.adapters.nats.config._probe_socket_daemon", return_value="absent"),
            patch(
                "voicecli.adapters.nats.synthesize_adapter.TtsNatsAdapter.run",
                new_callable=AsyncMock,
            ) as mock_run,
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
            patch("voicecli.adapters.nats.config._probe_socket_daemon", return_value="absent"),
            patch(
                "voicecli.adapters.nats.synthesize_adapter.TtsNatsAdapter.run",
                new_callable=AsyncMock,
            ) as mock_run,
        ):
            result = CliRunner().invoke(app, ["nats-serve", "tts", "--engine", "qwen-fast"])

        assert result.exit_code == 0, result.output
        assert mock_run.call_count == 1


class TestVramGuard:
    """Verify VRAM guard logic: exit 78 on live socket, proceed on stale."""

    def test_tts_live_socket_exits_78(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """nats-serve tts exits with code 78 when a live socket daemon is detected."""
        from unittest.mock import patch

        from typer.testing import CliRunner

        from voicecli.cli import app

        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")

        with patch("voicecli.adapters.nats.config._probe_socket_daemon", return_value="live"):
            result = CliRunner().invoke(app, ["nats-serve", "tts"])

        assert result.exit_code == 78, result.output

    def test_tts_stale_socket_proceeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """nats-serve tts proceeds normally when socket file is stale."""
        from unittest.mock import AsyncMock, patch

        from typer.testing import CliRunner

        from voicecli.cli import app

        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")

        with (
            patch("voicecli.adapters.nats.config._probe_socket_daemon", return_value="stale"),
            patch(
                "voicecli.adapters.nats.synthesize_adapter.TtsNatsAdapter.run",
                new_callable=AsyncMock,
            ),
        ):
            result = CliRunner().invoke(app, ["nats-serve", "tts"])

        assert result.exit_code == 0, result.output


class TestSttConnect:
    """Exercise STT NATS adapter CLI seam end-to-end via typer.testing.CliRunner."""

    def test_stt_connects_with_nkey_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI successfully starts STT adapter when NATS_NKEY_SEED_PATH is set."""
        from unittest.mock import AsyncMock, patch

        from typer.testing import CliRunner

        from voicecli.cli import app

        seed_file = tmp_path / "test.seed"
        seed_file.write_text("SUAXXX123FAKESEEDFORTEST")
        seed_file.chmod(0o600)
        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", str(seed_file))

        with (
            patch("voicecli.adapters.nats.config._probe_socket_daemon", return_value="absent"),
            patch(
                "voicecli.adapters.nats.transcribe_adapter.SttNatsAdapter.run",
                new_callable=AsyncMock,
            ) as mock_run,
        ):
            result = CliRunner().invoke(app, ["nats-serve", "stt", "--model", "base"])

        assert result.exit_code == 0, result.output
        assert mock_run.call_count == 1
        assert mock_run.call_args.args == ("nats://localhost:4222",)


class TestMissingNatsUrl:
    """Verify that missing NATS_URL env var causes exit code 2."""

    def test_tts_exits_2_when_nats_url_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """nats-serve tts exits with code 2 when NATS_URL is not set."""
        from unittest.mock import patch

        from typer.testing import CliRunner

        from voicecli.cli import app

        monkeypatch.delenv("NATS_URL", raising=False)

        with patch("voicecli.adapters.nats.config._probe_socket_daemon", return_value="absent"):
            result = CliRunner().invoke(app, ["nats-serve", "tts"])

        assert result.exit_code == 2, result.output

    def test_stt_exits_2_when_nats_url_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """nats-serve stt exits with code 2 when NATS_URL is not set."""
        from unittest.mock import patch

        from typer.testing import CliRunner

        from voicecli.cli import app

        monkeypatch.delenv("NATS_URL", raising=False)

        with patch("voicecli.adapters.nats.config._probe_socket_daemon", return_value="absent"):
            result = CliRunner().invoke(app, ["nats-serve", "stt"])

        assert result.exit_code == 2, result.output


class TestNatsUrlSchemeValidation:
    """Verify NATS_URL scheme guard: exit 2 on invalid scheme, warn on non-TLS."""

    def test_tts_exits_2_on_invalid_scheme(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """nats-serve tts exits 2 when NATS_URL has an invalid scheme."""
        from unittest.mock import patch

        from typer.testing import CliRunner

        from voicecli.cli import app

        # Arrange
        monkeypatch.setenv("NATS_URL", "ws://attacker.host:4222")

        # Act
        with patch("voicecli.adapters.nats.config._probe_socket_daemon", return_value="absent"):
            result = CliRunner().invoke(app, ["nats-serve", "tts"])

        # Assert
        assert result.exit_code == 2, result.output

    def test_stt_exits_2_on_invalid_scheme(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """nats-serve stt exits 2 when NATS_URL has an invalid scheme."""
        from unittest.mock import patch

        from typer.testing import CliRunner

        from voicecli.cli import app

        # Arrange
        monkeypatch.setenv("NATS_URL", "ws://attacker.host:4222")

        # Act
        with patch("voicecli.adapters.nats.config._probe_socket_daemon", return_value="absent"):
            result = CliRunner().invoke(app, ["nats-serve", "stt"])

        # Assert
        assert result.exit_code == 2, result.output

    def test_tts_exits_2_on_http_scheme(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """nats-serve tts exits 2 for http:// scheme."""
        from unittest.mock import patch

        from typer.testing import CliRunner

        from voicecli.cli import app

        # Arrange
        monkeypatch.setenv("NATS_URL", "http://localhost:4222")

        # Act
        with patch("voicecli.adapters.nats.config._probe_socket_daemon", return_value="absent"):
            result = CliRunner().invoke(app, ["nats-serve", "tts"])

        # Assert
        assert result.exit_code == 2, result.output

    def test_tts_warns_on_nats_scheme(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """nats-serve tts emits unencrypted warning for nats:// and proceeds."""
        import logging
        from unittest.mock import AsyncMock, patch

        from typer.testing import CliRunner

        from voicecli.cli import app

        # Arrange
        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")

        # Act
        with (
            caplog.at_level(logging.WARNING, logger="voicecli.nats-serve.tts"),
            patch("voicecli.adapters.nats.config._probe_socket_daemon", return_value="absent"),
            patch(
                "voicecli.adapters.nats.synthesize_adapter.TtsNatsAdapter.run",
                new_callable=AsyncMock,
            ) as mock_run,
        ):
            result = CliRunner().invoke(app, ["nats-serve", "tts"])

        # Assert
        assert result.exit_code == 0, result.output
        assert mock_run.call_count == 1
        assert any("unencrypted" in r.message for r in caplog.records)

    def test_tts_no_warning_on_tls_scheme(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """nats-serve tts does not warn for tls:// scheme."""
        import logging
        from unittest.mock import AsyncMock, patch

        from typer.testing import CliRunner

        from voicecli.cli import app

        # Arrange
        monkeypatch.setenv("NATS_URL", "tls://localhost:4222")

        # Act
        with (
            caplog.at_level(logging.WARNING, logger="voicecli.nats-serve.tts"),
            patch("voicecli.adapters.nats.config._probe_socket_daemon", return_value="absent"),
            patch(
                "voicecli.adapters.nats.synthesize_adapter.TtsNatsAdapter.run",
                new_callable=AsyncMock,
            ) as mock_run,
        ):
            result = CliRunner().invoke(app, ["nats-serve", "tts"])

        # Assert
        assert result.exit_code == 0, result.output
        assert mock_run.call_count == 1
        assert not any("unencrypted" in r.message for r in caplog.records)

    def test_stt_warns_on_nats_scheme(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """nats-serve stt emits unencrypted warning for nats:// and proceeds."""
        import logging
        from unittest.mock import AsyncMock, patch

        from typer.testing import CliRunner

        from voicecli.cli import app

        # Arrange
        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")

        # Act
        with (
            caplog.at_level(logging.WARNING, logger="voicecli.nats-serve.stt"),
            patch("voicecli.adapters.nats.config._probe_socket_daemon", return_value="absent"),
            patch(
                "voicecli.adapters.nats.transcribe_adapter.SttNatsAdapter.run",
                new_callable=AsyncMock,
            ) as mock_run,
        ):
            result = CliRunner().invoke(app, ["nats-serve", "stt"])

        # Assert
        assert result.exit_code == 0, result.output
        assert mock_run.call_count == 1
        assert any("unencrypted" in r.message for r in caplog.records)

    def test_tts_vram_guard_precedes_scheme_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VRAM guard (exit 78) fires before scheme validation — invalid URL + live socket exits 78."""
        from unittest.mock import patch

        from typer.testing import CliRunner

        from voicecli.cli import app

        # Arrange
        monkeypatch.setenv("NATS_URL", "ws://attacker.host:4222")

        # Act
        with patch("voicecli.adapters.nats.config._probe_socket_daemon", return_value="live"):
            result = CliRunner().invoke(app, ["nats-serve", "tts"])

        # Assert — VRAM guard wins; scheme check is never reached
        assert result.exit_code == 78, result.output

    def test_stt_vram_guard_precedes_scheme_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VRAM guard (exit 78) fires before scheme validation on STT path."""
        from unittest.mock import patch

        from typer.testing import CliRunner

        from voicecli.cli import app

        # Arrange
        monkeypatch.setenv("NATS_URL", "ws://attacker.host:4222")

        # Act
        with patch("voicecli.adapters.nats.config._probe_socket_daemon", return_value="live"):
            result = CliRunner().invoke(app, ["nats-serve", "stt"])

        # Assert — VRAM guard wins; scheme check is never reached
        assert result.exit_code == 78, result.output

    def test_stt_exits_2_on_http_scheme(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """nats-serve stt exits 2 for http:// scheme."""
        from unittest.mock import patch

        from typer.testing import CliRunner

        from voicecli.cli import app

        # Arrange
        monkeypatch.setenv("NATS_URL", "http://localhost:4222")

        # Act
        with patch("voicecli.adapters.nats.config._probe_socket_daemon", return_value="absent"):
            result = CliRunner().invoke(app, ["nats-serve", "stt"])

        # Assert
        assert result.exit_code == 2, result.output

    def test_stt_no_warning_on_tls_scheme(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """nats-serve stt does not warn for tls:// scheme."""
        import logging
        from unittest.mock import AsyncMock, patch

        from typer.testing import CliRunner

        from voicecli.cli import app

        # Arrange
        monkeypatch.setenv("NATS_URL", "tls://localhost:4222")

        # Act
        with (
            caplog.at_level(logging.WARNING, logger="voicecli.nats-serve.stt"),
            patch("voicecli.adapters.nats.config._probe_socket_daemon", return_value="absent"),
            patch(
                "voicecli.adapters.nats.transcribe_adapter.SttNatsAdapter.run",
                new_callable=AsyncMock,
            ) as mock_run,
        ):
            result = CliRunner().invoke(app, ["nats-serve", "stt"])

        # Assert
        assert result.exit_code == 0, result.output
        assert mock_run.call_count == 1
        assert not any("unencrypted" in r.message for r in caplog.records)
