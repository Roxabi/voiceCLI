"""Tests for samples from-url command."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from voicecli.samples import _check_tool, from_url


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def samples_env(tmp_path, monkeypatch):
    """Set up a temporary samples directory and return (samples_dir, fake_run_factory)."""
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    monkeypatch.setattr("voicecli.samples.SAMPLES_DIR", samples_dir)
    monkeypatch.setattr("voicecli.samples.ACTIVE_FILE", samples_dir / ".active")

    def make_fake_run(*, also_write_dest: bool = False):
        """Return a fake subprocess.run that simulates yt-dlp creating a file."""

        def fake_run(cmd, *, check=False):
            if cmd[0] == "yt-dlp":
                o_idx = cmd.index("-o")
                pattern = cmd[o_idx + 1]
                fake_path = Path(pattern.replace("%(ext)s", "wav"))
                fake_path.write_bytes(b"fake audio data")
            elif cmd[0] == "ffmpeg" and also_write_dest:
                # Write a fake dest file (last positional arg)
                Path(cmd[-1]).write_bytes(b"fake wav")

        return fake_run

    return samples_dir, make_fake_run


# ── _check_tool ──────────────────────────────────────────────────────────────


class TestCheckTool:
    def test_found(self):
        # Arrange / Act / Assert
        with patch("voicecli.samples.shutil.which", return_value="/usr/bin/git"):
            _check_tool("git")  # should not raise

    def test_not_found_yt_dlp(self):
        # Arrange / Act / Assert
        with patch("voicecli.samples.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="yt-dlp.*not found"):
                _check_tool("yt-dlp")

    def test_not_found_ffmpeg(self):
        # Arrange / Act / Assert
        with patch("voicecli.samples.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="ffmpeg.*not found"):
                _check_tool("ffmpeg")


# ── from_url unit tests ─────────────────────────────────────────────────────


class TestFromUrl:
    @patch("voicecli.samples.subprocess.run")
    @patch("voicecli.samples.shutil.which", return_value="/usr/bin/ok")
    def test_downloads_extracts_and_normalizes(self, _mock_which, mock_run, samples_env):
        # Arrange
        samples_dir, make_fake_run = samples_env
        mock_run.side_effect = make_fake_run(also_write_dest=True)

        # Act
        dest = from_url("https://youtube.com/watch?v=test", "mysample", start=5.0, duration=20.0)

        # Assert
        assert dest == samples_dir / "mysample.wav"
        assert dest.exists()
        assert mock_run.call_count == 2

        yt_args = mock_run.call_args_list[0][0][0]
        assert yt_args[0] == "yt-dlp"
        assert "--no-config" in yt_args
        assert "--" in yt_args
        assert "https://youtube.com/watch?v=test" in yt_args

        ff_args = mock_run.call_args_list[1][0][0]
        assert ff_args[0] == "ffmpeg"
        ss_idx = ff_args.index("-ss")
        assert ff_args[ss_idx + 1] == "5.0"
        t_idx = ff_args.index("-t")
        assert ff_args[t_idx + 1] == "20.0"
        assert "24000" in ff_args

    @patch("voicecli.samples.shutil.which", return_value=None)
    def test_missing_yt_dlp(self, _mock_which):
        # Act / Assert
        with pytest.raises(RuntimeError, match="yt-dlp"):
            from_url("https://youtube.com/watch?v=test", "sample")

    @patch("voicecli.samples.shutil.which")
    def test_missing_ffmpeg_only(self, mock_which):
        # Arrange — yt-dlp found, ffmpeg not
        mock_which.side_effect = lambda name: "/usr/bin/yt-dlp" if name == "yt-dlp" else None

        # Act / Assert
        with pytest.raises(RuntimeError, match="ffmpeg.*not found"):
            from_url("https://youtube.com/watch?v=test", "sample")

    @patch("voicecli.samples.subprocess.run")
    @patch("voicecli.samples.shutil.which", return_value="/usr/bin/ok")
    def test_appends_wav_extension(self, _mock_which, mock_run, samples_env):
        # Arrange
        _samples_dir, make_fake_run = samples_env
        mock_run.side_effect = make_fake_run()

        # Act
        dest = from_url("https://example.com/video", "no_ext")

        # Assert
        assert dest.name == "no_ext.wav"

    @patch("voicecli.samples.subprocess.run")
    @patch("voicecli.samples.shutil.which", return_value="/usr/bin/ok")
    def test_no_output_from_ytdlp_raises(self, _mock_which, mock_run, samples_env):
        # Arrange — yt-dlp runs but creates no file
        mock_run.return_value = None

        # Act / Assert
        with pytest.raises(RuntimeError, match="did not produce"):
            from_url("https://example.com/video", "sample")

    @patch("voicecli.samples.subprocess.run")
    @patch("voicecli.samples.shutil.which", return_value="/usr/bin/ok")
    def test_ytdlp_failure_raises_runtime_error(self, _mock_which, mock_run, samples_env):
        # Arrange
        mock_run.side_effect = subprocess.CalledProcessError(1, "yt-dlp")

        # Act / Assert
        with pytest.raises(RuntimeError, match="yt-dlp failed"):
            from_url("https://example.com/video", "sample")

    @patch("voicecli.samples.subprocess.run")
    @patch("voicecli.samples.shutil.which", return_value="/usr/bin/ok")
    def test_ffmpeg_failure_raises_runtime_error(self, _mock_which, mock_run, samples_env):
        # Arrange — yt-dlp succeeds, ffmpeg fails
        _samples_dir, make_fake_run = samples_env
        call_count = 0

        def ytdlp_ok_ffmpeg_fail(cmd, *, check=False):
            nonlocal call_count
            call_count += 1
            if cmd[0] == "yt-dlp":
                make_fake_run()(cmd, check=check)
            else:
                raise subprocess.CalledProcessError(1, "ffmpeg")

        mock_run.side_effect = ytdlp_ok_ffmpeg_fail

        # Act / Assert
        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            from_url("https://example.com/video", "sample")

    def test_negative_start_raises(self):
        # Act / Assert
        with pytest.raises(ValueError, match="start must be non-negative"):
            from_url("https://example.com/video", "sample", start=-5.0)

    def test_zero_duration_raises(self):
        # Act / Assert
        with pytest.raises(ValueError, match="duration must be positive"):
            from_url("https://example.com/video", "sample", duration=0)

    def test_non_http_url_raises(self):
        # Act / Assert
        with pytest.raises(ValueError, match="Only http/https"):
            from_url("file:///etc/passwd", "sample")

    @patch("voicecli.samples.subprocess.run")
    @patch("voicecli.samples.shutil.which", return_value="/usr/bin/ok")
    def test_path_traversal_stripped(self, _mock_which, mock_run, samples_env):
        # Arrange
        samples_dir, make_fake_run = samples_env
        mock_run.side_effect = make_fake_run()

        # Act
        dest = from_url("https://example.com/video", "../../../tmp/evil")

        # Assert — name is sanitized to bare filename
        assert dest.parent == samples_dir
        assert dest.name == "evil.wav"

    @patch("voicecli.samples.subprocess.run")
    @patch("voicecli.samples.shutil.which", return_value="/usr/bin/ok")
    def test_uses_default_start_and_duration(self, _mock_which, mock_run, samples_env):
        # Arrange
        _samples_dir, make_fake_run = samples_env
        mock_run.side_effect = make_fake_run()

        # Act
        from_url("https://example.com/video", "defaults")

        # Assert — ffmpeg gets default start=10.0, duration=30.0
        ff_args = mock_run.call_args_list[1][0][0]
        ss_idx = ff_args.index("-ss")
        assert ff_args[ss_idx + 1] == "10.0"
        t_idx = ff_args.index("-t")
        assert ff_args[t_idx + 1] == "30.0"


# ── CLI command tests ────────────────────────────────────────────────────────


class TestSamplesFromUrlCommand:
    def test_happy_path(self):
        from typer.testing import CliRunner

        from voicecli.cli import app

        # Arrange
        runner = CliRunner()
        fake_dest = Path("TTS/samples/myvoice.wav")

        with patch("voicecli.samples.from_url", return_value=fake_dest) as mock_from_url:
            # Act
            result = runner.invoke(app, ["samples", "from-url", "https://yt.com/v", "myvoice"])

            # Assert
            assert result.exit_code == 0
            assert "Added" in result.stdout
            mock_from_url.assert_called_once_with(
                "https://yt.com/v", "myvoice", start=10.0, duration=30.0
            )

    def test_use_flag_sets_active(self):
        from typer.testing import CliRunner

        from voicecli.cli import app

        # Arrange
        runner = CliRunner()
        fake_dest = Path("TTS/samples/myvoice.wav")

        with (
            patch("voicecli.samples.from_url", return_value=fake_dest),
            patch("voicecli.samples.set_active") as mock_set_active,
        ):
            # Act
            result = runner.invoke(
                app, ["samples", "from-url", "https://yt.com/v", "myvoice", "--use"]
            )

            # Assert
            assert result.exit_code == 0
            assert "Active sample set to: myvoice.wav" in result.stdout
            mock_set_active.assert_called_once_with("myvoice.wav")

    def test_use_flag_absent_does_not_set_active(self):
        from typer.testing import CliRunner

        from voicecli.cli import app

        # Arrange
        runner = CliRunner()
        fake_dest = Path("TTS/samples/myvoice.wav")

        with (
            patch("voicecli.samples.from_url", return_value=fake_dest),
            patch("voicecli.samples.set_active") as mock_set_active,
        ):
            # Act
            result = runner.invoke(app, ["samples", "from-url", "https://yt.com/v", "myvoice"])

            # Assert
            assert result.exit_code == 0
            mock_set_active.assert_not_called()

    def test_runtime_error_exits_1(self):
        from typer.testing import CliRunner

        from voicecli.cli import app

        # Arrange
        runner = CliRunner()

        with patch("voicecli.samples.from_url", side_effect=RuntimeError("yt-dlp not found")):
            # Act
            result = runner.invoke(app, ["samples", "from-url", "https://yt.com/v", "myvoice"])

            # Assert
            assert result.exit_code == 1

    def test_value_error_exits_1(self):
        from typer.testing import CliRunner

        from voicecli.cli import app

        # Arrange
        runner = CliRunner()

        with patch("voicecli.samples.from_url", side_effect=ValueError("Only http/https URLs")):
            # Act
            result = runner.invoke(app, ["samples", "from-url", "file:///etc/passwd", "evil"])

            # Assert
            assert result.exit_code == 1

    def test_custom_start_and_duration(self):
        from typer.testing import CliRunner

        from voicecli.cli import app

        # Arrange
        runner = CliRunner()
        fake_dest = Path("TTS/samples/myvoice.wav")

        with patch("voicecli.samples.from_url", return_value=fake_dest) as mock_from_url:
            # Act
            result = runner.invoke(
                app,
                [
                    "samples",
                    "from-url",
                    "https://yt.com/v",
                    "myvoice",
                    "--start",
                    "5",
                    "--duration",
                    "20",
                ],
            )

            # Assert
            assert result.exit_code == 0
            mock_from_url.assert_called_once_with(
                "https://yt.com/v", "myvoice", start=5.0, duration=20.0
            )
