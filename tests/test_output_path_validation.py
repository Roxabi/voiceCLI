"""Tests for output path validation (#57)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voicecli.api import _validate_output_path, clone, generate


class TestValidateOutputPath:
    def test_valid_path_inside_base(self, tmp_path):
        """Valid path inside base works."""
        base = tmp_path / "base"
        base.mkdir()
        out = base / "test.wav"
        result = _validate_output_path(out, allowed_base=base)
        assert result == out.resolve()
        assert result.parent.exists()

    def test_path_escaping_base_raises(self, tmp_path):
        """Path with .. escaping base raises ValueError."""
        base = tmp_path / "base"
        base.mkdir()
        evil = base / ".." / "escape.wav"
        with pytest.raises(ValueError, match="escapes"):
            _validate_output_path(evil, allowed_base=base)

    def test_absolute_path_outside_base_raises(self, tmp_path):
        """Absolute path outside base raises ValueError."""
        base = tmp_path / "base"
        base.mkdir()
        outside = tmp_path / "outside" / "test.wav"
        with pytest.raises(ValueError, match="escapes"):
            _validate_output_path(outside, allowed_base=base)

    def test_absolute_path_outside_base_with_bypass(self, tmp_path):
        """Bypass allows absolute path outside base."""
        base = tmp_path / "base"
        base.mkdir()
        outside = tmp_path / "outside" / "test.wav"
        result = _validate_output_path(outside, allowed_base=base, cli_bypass=True)
        assert result == outside.resolve()

    def test_relative_path_inside_base_works(self, tmp_path):
        """Relative path staying inside base works."""
        base = tmp_path / "base"
        base.mkdir()
        out = base / "subdir" / "test.wav"
        result = _validate_output_path(out, allowed_base=base)
        assert result.parent.exists()

    def test_symlink_escaping_base_raises(self, tmp_path):
        """Symlink pointing outside base is blocked."""
        base = tmp_path / "base"
        base.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        link = base / "link.wav"
        link.symlink_to(outside / "evil.wav")
        with pytest.raises(ValueError, match="escapes"):
            _validate_output_path(link, allowed_base=base)

    def test_nested_nonexistent_dir_created(self, tmp_path):
        """Nested non-existent directory inside base is auto-created."""
        base = tmp_path / "base"
        base.mkdir()
        out = base / "a" / "b" / "c" / "test.wav"
        result = _validate_output_path(out, allowed_base=base)
        assert result.parent.exists()


class TestGenerateOutputValidation:
    """Integration tests for generate() output validation."""

    def test_generate_rejects_escape_path(self, tmp_path):
        """generate() rejects path escaping base."""
        # Arrange
        base = tmp_path / "base"
        base.mkdir()
        evil = base / ".." / "escape.wav"

        # Act + Assert
        with (
            patch("voicecli.config.load_defaults", return_value={}),
            patch("voicecli.engine.get_engine", side_effect=Exception("should not reach")),
            pytest.raises(ValueError, match="escapes"),
        ):
            generate("test", output=evil)

    def test_generate_bypass_with_cli_flag(self, tmp_path):
        """generate() allows outside path with _cli_bypass=True."""
        # Arrange
        outside = tmp_path / "outside" / "test.wav"
        mock_engine = MagicMock()
        mock_engine.generate.return_value = outside

        # Act
        with (
            patch("voicecli.config.load_defaults", return_value={}),
            patch("voicecli.engine.get_engine", return_value=mock_engine),
        ):
            result = generate("test", output=outside, _cli_bypass=True, engine="chatterbox")

        # Assert
        assert result.wav_path == outside.resolve()


class TestCloneOutputValidation:
    """Integration tests for clone() output validation."""

    def test_clone_rejects_escape_path(self, tmp_path):
        """clone() rejects path escaping base."""
        # Arrange
        base = tmp_path / "base"
        base.mkdir()
        evil = base / ".." / "escape.wav"
        ref = tmp_path / "ref.wav"
        ref.write_bytes(b"fake audio")

        # Act + Assert
        with (
            patch("voicecli.config.load_defaults", return_value={}),
            patch("voicecli.engine.get_engine", side_effect=Exception("should not reach")),
            pytest.raises(ValueError, match="escapes"),
        ):
            clone("test", ref=ref, output=evil)

    def test_clone_bypass_with_cli_flag(self, tmp_path):
        """clone() allows outside path with _cli_bypass=True (mirror of generate)."""
        # Arrange
        outside = tmp_path / "outside" / "test.wav"
        ref = tmp_path / "ref.wav"
        ref.write_bytes(b"fake audio")
        mock_engine = MagicMock()
        mock_engine.clone.return_value = outside

        # Act
        with (
            patch("voicecli.config.load_defaults", return_value={}),
            patch("voicecli.engine.get_engine", return_value=mock_engine),
        ):
            result = clone("test", ref=ref, output=outside, _cli_bypass=True, engine="chatterbox")

        # Assert
        assert result.wav_path == outside.resolve()


class TestCLIBypass:
    """Tests that CLI commands forward _cli_bypass=True when --output is set."""

    def test_generate_cli_forwards_cli_bypass_when_output_set(self, tmp_path):
        """`voicecli generate --output X` must pass _cli_bypass=True to api.generate."""
        from typer.testing import CliRunner

        from voicecli.cli import app

        outside = tmp_path / "outside" / "test.wav"
        fake_result = MagicMock(wav_path=outside, mp3_path=None)

        with patch("voicecli.api.generate", return_value=fake_result) as mock_gen:
            runner = CliRunner()
            result = runner.invoke(app, ["generate", "hello", "--output", str(outside)])

        assert result.exit_code == 0, result.output
        assert mock_gen.call_args.kwargs.get("_cli_bypass") is True

    def test_generate_cli_does_not_bypass_without_output(self):
        """`voicecli generate` (no --output) must pass _cli_bypass=False."""
        from typer.testing import CliRunner

        from voicecli.cli import app

        fake_result = MagicMock(wav_path=Path("/tmp/out.wav"), mp3_path=None)

        with patch("voicecli.api.generate", return_value=fake_result) as mock_gen:
            runner = CliRunner()
            result = runner.invoke(app, ["generate", "hello"])

        assert result.exit_code == 0, result.output
        assert mock_gen.call_args.kwargs.get("_cli_bypass") is False

    def test_clone_cli_forwards_cli_bypass_when_output_set(self, tmp_path):
        """`voicecli clone --output X` must pass _cli_bypass=True to api.clone."""
        from typer.testing import CliRunner

        from voicecli.cli import app

        ref = tmp_path / "ref.wav"
        ref.write_bytes(b"fake audio")
        outside = tmp_path / "outside" / "test.wav"
        fake_result = MagicMock(wav_path=outside, mp3_path=None)

        with patch("voicecli.api.clone", return_value=fake_result) as mock_clone:
            runner = CliRunner()
            result = runner.invoke(
                app, ["clone", "hello", "--ref", str(ref), "--output", str(outside)]
            )

        assert result.exit_code == 0, result.output
        assert mock_clone.call_args.kwargs.get("_cli_bypass") is True
