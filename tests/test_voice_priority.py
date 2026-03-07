"""Tests for voice field priority: CLI flag > frontmatter > voicecli.toml."""

import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def md_with_voice(tmp_path):
    """Create a .md file with voice in frontmatter."""
    p = tmp_path / "test.md"
    p.write_text(
        textwrap.dedent("""\
        ---
        voice: Dylan
        ---

        Hello world.
        """)
    )
    return p


@pytest.fixture
def md_without_voice(tmp_path):
    """Create a .md file without voice in frontmatter."""
    p = tmp_path / "test.md"
    p.write_text(
        textwrap.dedent("""\
        ---
        language: French
        ---

        Bonjour le monde.
        """)
    )
    return p


@pytest.fixture
def _mock_engine():
    """Mock get_engine and daemon to avoid loading real models."""
    engine = MagicMock()
    engine.generate.return_value = ([], 24000)
    with (
        patch("voicecli.engine.get_engine", return_value=engine),
        patch("voicecli.api._try_daemon", return_value=None),
    ):
        yield engine


def _called_voice(mock_engine):
    """Extract voice from engine.generate(text, voice, out, ...) call."""
    return mock_engine.generate.call_args[0][1]


class TestVoicePriority:
    """Voice priority: CLI flag > frontmatter > voicecli.toml > hardcoded default."""

    def test_frontmatter_overrides_toml(self, md_with_voice, _mock_engine):
        """Frontmatter voice=Dylan should override toml voice=Ono_Anna."""
        from voicecli.cli import app
        from typer.testing import CliRunner

        runner = CliRunner()
        toml_cfg = {"voice": "Ono_Anna"}
        with patch("voicecli.config.load_defaults", return_value=toml_cfg):
            result = runner.invoke(app, ["generate", str(md_with_voice)])

        assert result.exit_code == 0, result.output
        # voice is 2nd positional arg: generate(text, voice, output_path, ...)
        assert _called_voice(_mock_engine) == "Dylan"

    def test_cli_flag_overrides_frontmatter(self, md_with_voice, _mock_engine):
        """CLI --voice Ryan should override frontmatter voice=Dylan."""
        from voicecli.cli import app
        from typer.testing import CliRunner

        runner = CliRunner()
        toml_cfg = {"voice": "Ono_Anna"}
        with patch("voicecli.config.load_defaults", return_value=toml_cfg):
            result = runner.invoke(app, ["generate", "--voice", "Ryan", str(md_with_voice)])

        assert result.exit_code == 0, result.output
        assert _called_voice(_mock_engine) == "Ryan"

    def test_toml_used_when_no_frontmatter(self, md_without_voice, _mock_engine):
        """Toml voice=Ono_Anna should apply when frontmatter has no voice."""
        from voicecli.cli import app
        from typer.testing import CliRunner

        runner = CliRunner()
        toml_cfg = {"voice": "Ono_Anna"}
        with patch("voicecli.config.load_defaults", return_value=toml_cfg):
            result = runner.invoke(app, ["generate", str(md_without_voice)])

        assert result.exit_code == 0, result.output
        assert _called_voice(_mock_engine) == "Ono_Anna"

    def test_no_voice_when_nothing_set(self, md_without_voice, _mock_engine):
        """No voice passed to engine when neither CLI, frontmatter, nor toml set it."""
        from voicecli.cli import app
        from typer.testing import CliRunner

        runner = CliRunner()
        with patch("voicecli.config.load_defaults", return_value={}):
            result = runner.invoke(app, ["generate", str(md_without_voice)])

        assert result.exit_code == 0, result.output
        assert _called_voice(_mock_engine) is None
