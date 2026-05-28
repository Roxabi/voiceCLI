"""Tests for output path validation (#57)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voicecli.api import _validate_output_path, clone, generate
from voicecli.utils import OUTPUT_DIR, UNRESTRICTED


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
        with pytest.raises(ValueError, match="outside"):
            _validate_output_path(evil, allowed_base=base)

    def test_absolute_path_outside_base_raises(self, tmp_path):
        """Absolute path outside base raises ValueError."""
        base = tmp_path / "base"
        base.mkdir()
        outside = tmp_path / "outside" / "test.wav"
        with pytest.raises(ValueError, match="outside"):
            _validate_output_path(outside, allowed_base=base)

    def test_unrestricted_allows_absolute_path_outside_base(self, tmp_path):
        """UNRESTRICTED allows absolute path outside base and skips mkdir."""
        outside = tmp_path / "outside" / "test.wav"
        result = _validate_output_path(outside, allowed_base=UNRESTRICTED)
        assert result == outside.resolve()
        # UNRESTRICTED does NOT auto-create parent dirs (caller owns boundary)
        assert not outside.parent.exists()

    def test_relative_path_inside_base_works(self, tmp_path):
        """Relative path staying inside base works."""
        base = tmp_path / "base"
        base.mkdir()
        out = base / "subdir" / "test.wav"
        result = _validate_output_path(out, allowed_base=base)
        assert result.parent.exists()

    def test_symlink_escaping_base_raises(self, tmp_path):
        """Symlink pointing outside base is blocked after resolve()."""
        base = tmp_path / "base"
        base.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "evil.wav"
        target.touch()
        link = base / "link.wav"
        link.symlink_to(target)
        with pytest.raises(ValueError, match="outside"):
            _validate_output_path(link, allowed_base=base)

    def test_nested_nonexistent_dir_created(self, tmp_path):
        """Nested non-existent directory inside base is auto-created."""
        base = tmp_path / "base"
        base.mkdir()
        out = base / "a" / "b" / "c" / "test.wav"
        result = _validate_output_path(out, allowed_base=base)
        assert result.parent.exists()

    def test_default_output_dir_branch(self, tmp_path, monkeypatch):
        """Default OUTPUT_DIR base is honored when no allowed_base override is provided by callers."""
        # Redirect OUTPUT_DIR → tmp_path so the test is hermetic.
        fake_base = tmp_path / "voices_out"
        fake_base.mkdir()
        monkeypatch.setattr("voicecli.api.OUTPUT_DIR", fake_base)
        inside = fake_base / "x.wav"
        result = _validate_output_path(inside, allowed_base=fake_base)
        assert result == inside.resolve()
        # Default OUTPUT_DIR constant in utils.py points into ~/.roxabi/voicecli/TTS/voices_out
        assert "voices_out" in str(OUTPUT_DIR)


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
            pytest.raises(ValueError, match="outside"),
        ):
            generate("test", output=evil)

    def test_generate_unrestricted_base_allows_outside_path(self, tmp_path):
        """generate(allowed_base=UNRESTRICTED) accepts outside path."""
        # Arrange
        outside = tmp_path / "outside" / "test.wav"
        outside.parent.mkdir(parents=True, exist_ok=True)
        mock_engine = MagicMock()
        mock_engine.generate.return_value = outside

        # Act
        with (
            patch("voicecli.config.load_defaults", return_value={}),
            patch("voicecli.engine.get_engine", return_value=mock_engine),
        ):
            result = generate(
                "test", output=outside, allowed_base=UNRESTRICTED, engine="chatterbox"
            )

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
            pytest.raises(ValueError, match="outside"),
        ):
            clone("test", ref=ref, output=evil)

    def test_clone_unrestricted_base_allows_outside_path(self, tmp_path):
        """clone(allowed_base=UNRESTRICTED) accepts outside path (mirror of generate)."""
        # Arrange
        outside = tmp_path / "outside" / "test.wav"
        outside.parent.mkdir(parents=True, exist_ok=True)
        ref = tmp_path / "ref.wav"
        ref.write_bytes(b"fake audio")
        mock_engine = MagicMock()
        mock_engine.clone.return_value = outside

        # Act
        with (
            patch("voicecli.config.load_defaults", return_value={}),
            patch("voicecli.engine.get_engine", return_value=mock_engine),
        ):
            result = clone(
                "test",
                ref=ref,
                output=outside,
                allowed_base=UNRESTRICTED,
                engine="chatterbox",
            )

        # Assert
        assert result.wav_path == outside.resolve()


class TestCLIBypass:
    """Tests that CLI commands forward allowed_base=UNRESTRICTED when --output is set."""

    def test_generate_cli_sets_unrestricted_when_output_set(self, tmp_path):
        """`voicecli generate --output X` must pass allowed_base=UNRESTRICTED to api.generate."""
        from typer.testing import CliRunner

        from voicecli.cli import app

        outside = tmp_path / "outside" / "test.wav"
        fake_result = MagicMock(wav_path=outside, mp3_path=None)

        with patch("voicecli.api.generate", return_value=fake_result) as mock_gen:
            runner = CliRunner()
            result = runner.invoke(app, ["generate", "hello", "--output", str(outside)])

        assert result.exit_code == 0, result.output
        assert mock_gen.call_args.kwargs.get("allowed_base") is UNRESTRICTED

    def test_generate_cli_uses_output_dir_without_output(self):
        """`voicecli generate` (no --output) must pass allowed_base=OUTPUT_DIR."""
        from typer.testing import CliRunner

        from voicecli.cli import app

        fake_result = MagicMock(wav_path=Path("/tmp/out.wav"), mp3_path=None)

        with patch("voicecli.api.generate", return_value=fake_result) as mock_gen:
            runner = CliRunner()
            result = runner.invoke(app, ["generate", "hello"])

        assert result.exit_code == 0, result.output
        assert mock_gen.call_args.kwargs.get("allowed_base") == OUTPUT_DIR

    def test_clone_cli_sets_unrestricted_when_output_set(self, tmp_path):
        """`voicecli clone --output X` must pass allowed_base=UNRESTRICTED to api.clone."""
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
        assert mock_clone.call_args.kwargs.get("allowed_base") is UNRESTRICTED
