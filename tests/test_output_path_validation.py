"""Tests for output path validation (#57)."""

from pathlib import Path

import pytest

from voicecli.api import _validate_output_path


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
        from unittest.mock import MagicMock, patch

        from voicecli.api import generate

        base = tmp_path / "base"
        base.mkdir()
        evil = base / ".." / "escape.wav"

        with (
            patch("voicecli.config.load_defaults", return_value={}),
            patch("voicecli.engine.get_engine", side_effect=Exception("should not reach")),
            pytest.raises(ValueError, match="escapes"),
        ):
            generate("test", output=evil)

    def test_generate_bypass_with_cli_flag(self, tmp_path):
        """generate() allows outside path with _cli_bypass=True."""
        from unittest.mock import MagicMock, patch

        from voicecli.api import generate

        outside = tmp_path / "outside" / "test.wav"
        mock_engine = MagicMock()
        mock_engine.generate.return_value = outside

        with (
            patch("voicecli.config.load_defaults", return_value={}),
            patch("voicecli.engine.get_engine", return_value=mock_engine),
        ):
            # Should NOT raise - bypass active
            # Use chatterbox engine to avoid daemon path
            generate("test", output=outside, _cli_bypass=True, engine="chatterbox")


class TestCloneOutputValidation:
    """Integration tests for clone() output validation."""

    def test_clone_rejects_escape_path(self, tmp_path):
        """clone() rejects path escaping base."""
        from unittest.mock import patch

        from voicecli.api import clone

        base = tmp_path / "base"
        base.mkdir()
        evil = base / ".." / "escape.wav"
        ref = tmp_path / "ref.wav"
        ref.write_bytes(b"fake audio")

        with (
            patch("voicecli.config.load_defaults", return_value={}),
            patch("voicecli.engine.get_engine", side_effect=Exception("should not reach")),
            pytest.raises(ValueError, match="escapes"),
        ):
            clone("test", ref=ref, output=evil)


class TestCLIBypass:
    """Tests for CLI bypass functionality."""

    @pytest.mark.skip(reason="Requires CLI _cli_bypass integration (V4-T16)")
    def test_cli_output_flag_bypasses_validation(self, tmp_path, monkeypatch):
        """CLI --output flag should bypass validation for outside paths."""
        import subprocess

        outside = tmp_path / "outside" / "test.wav"
        # This would fail without bypass
        result = subprocess.run(
            ["uv", "run", "voicecli", "generate", "test", "--output", str(outside)],
            capture_output=True,
            text=True,
        )
        # Should NOT contain "escapes" error
        assert "escapes" not in result.stderr
