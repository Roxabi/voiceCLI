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
def _mock_daemon():
    """Mock DaemonSynthesisAdapter so Qwen generate goes through the socket path.

    Returns a mock for _try_socket whose call args contain the voice field.
    """
    engine = MagicMock()
    engine.generate.return_value = Path("/tmp/fake.wav")
    mock_try = MagicMock(return_value=Path("/tmp/fake.wav"))
    with (
        patch("voicecli.engines.engine.get_engine", return_value=engine),
        patch(
            "voicecli.adapters.synthesis.DaemonSynthesisAdapter._wait_for_socket",
            return_value=True,
        ),
        patch(
            "voicecli.adapters.synthesis.DaemonSynthesisAdapter._try_socket",
            mock_try,
        ),
    ):
        yield mock_try


def _called_voice(mock_try_socket):
    """Extract voice from the daemon request dict passed to _try_socket."""
    return mock_try_socket.call_args[0][0]["voice"]


class TestVoicePriority:
    """Voice priority: CLI flag > frontmatter > voicecli.toml > hardcoded default."""

    def test_frontmatter_overrides_toml(self, md_with_voice, _mock_daemon):
        """Frontmatter voice=Dylan should override toml voice=Ono_Anna."""
        from voicecli.cli import app
        from typer.testing import CliRunner

        runner = CliRunner()
        toml_cfg = {"voice": "Ono_Anna"}
        with patch("voicecli.core.config.load_defaults", return_value=toml_cfg):
            result = runner.invoke(app, ["generate", str(md_with_voice)])

        assert result.exit_code == 0, result.output
        assert _called_voice(_mock_daemon) == "Dylan"

    def test_cli_flag_overrides_frontmatter(self, md_with_voice, _mock_daemon):
        """CLI --voice Ryan should override frontmatter voice=Dylan."""
        from voicecli.cli import app
        from typer.testing import CliRunner

        runner = CliRunner()
        toml_cfg = {"voice": "Ono_Anna"}
        with patch("voicecli.core.config.load_defaults", return_value=toml_cfg):
            result = runner.invoke(app, ["generate", "--voice", "Ryan", str(md_with_voice)])

        assert result.exit_code == 0, result.output
        assert _called_voice(_mock_daemon) == "Ryan"

    def test_toml_used_when_no_frontmatter(self, md_without_voice, _mock_daemon):
        """Toml voice=Ono_Anna should apply when frontmatter has no voice."""
        from voicecli.cli import app
        from typer.testing import CliRunner

        runner = CliRunner()
        toml_cfg = {"voice": "Ono_Anna"}
        with patch("voicecli.core.config.load_defaults", return_value=toml_cfg):
            result = runner.invoke(app, ["generate", str(md_without_voice)])

        assert result.exit_code == 0, result.output
        assert _called_voice(_mock_daemon) == "Ono_Anna"

    def test_no_voice_when_nothing_set(self, md_without_voice, _mock_daemon):
        """No voice passed to engine when neither CLI, frontmatter, nor toml set it."""
        from voicecli.cli import app
        from typer.testing import CliRunner

        runner = CliRunner()
        with patch("voicecli.core.config.load_defaults", return_value={}):
            result = runner.invoke(app, ["generate", str(md_without_voice)])

        assert result.exit_code == 0, result.output
        assert _called_voice(_mock_daemon) is None
