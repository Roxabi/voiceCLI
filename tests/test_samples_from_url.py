"""Tests for samples from-url command."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from voicecli.samples import _check_tool, from_url


class TestCheckTool:
    def test_found(self):
        with patch("shutil.which", return_value="/usr/bin/git"):
            _check_tool("git")  # should not raise

    def test_not_found_yt_dlp(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="yt-dlp.*not found"):
                _check_tool("yt-dlp")

    def test_not_found_ffmpeg(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="ffmpeg.*not found"):
                _check_tool("ffmpeg")


class TestFromUrl:
    @patch("voicecli.samples.subprocess.run")
    @patch("voicecli.samples.shutil.which", return_value="/usr/bin/ok")
    def test_downloads_extracts_and_normalizes(self, mock_which, mock_run, tmp_path, monkeypatch):
        """Verify yt-dlp and ffmpeg are called with correct args."""
        samples_dir = tmp_path / "samples"
        samples_dir.mkdir()
        monkeypatch.setattr("voicecli.samples.SAMPLES_DIR", samples_dir)
        monkeypatch.setattr("voicecli.samples.ACTIVE_FILE", samples_dir / ".active")

        # yt-dlp creates a file in the temp dir — simulate this
        def fake_run(cmd, *, check=False):
            if cmd[0] == "yt-dlp":
                # Find the -o arg to know where to create the fake file
                o_idx = cmd.index("-o")
                pattern = cmd[o_idx + 1]
                # yt-dlp replaces %(ext)s with wav
                fake_path = Path(pattern.replace("%(ext)s", "wav"))
                fake_path.write_bytes(b"fake audio data")

        mock_run.side_effect = fake_run

        dest = from_url("https://youtube.com/watch?v=test", "mysample", start=5.0, duration=20.0)

        assert dest == samples_dir / "mysample.wav"
        assert mock_run.call_count == 2

        # First call: yt-dlp
        yt_call = mock_run.call_args_list[0]
        assert yt_call[0][0][0] == "yt-dlp"
        assert "-x" in yt_call[0][0]
        assert "https://youtube.com/watch?v=test" in yt_call[0][0]

        # Second call: ffmpeg
        ff_call = mock_run.call_args_list[1]
        ff_args = ff_call[0][0]
        assert ff_args[0] == "ffmpeg"
        assert "-ss" in ff_args
        ss_idx = ff_args.index("-ss")
        assert ff_args[ss_idx + 1] == "5.0"
        t_idx = ff_args.index("-t")
        assert ff_args[t_idx + 1] == "20.0"
        assert "-ac" in ff_args
        assert "24000" in ff_args

    @patch("voicecli.samples.shutil.which", return_value=None)
    def test_missing_yt_dlp(self, mock_which):
        with pytest.raises(RuntimeError, match="yt-dlp"):
            from_url("https://youtube.com/watch?v=test", "sample")

    @patch("voicecli.samples.subprocess.run")
    @patch("voicecli.samples.shutil.which", return_value="/usr/bin/ok")
    def test_appends_wav_extension(self, mock_which, mock_run, tmp_path, monkeypatch):
        samples_dir = tmp_path / "samples"
        samples_dir.mkdir()
        monkeypatch.setattr("voicecli.samples.SAMPLES_DIR", samples_dir)
        monkeypatch.setattr("voicecli.samples.ACTIVE_FILE", samples_dir / ".active")

        def fake_run(cmd, *, check=False):
            if cmd[0] == "yt-dlp":
                o_idx = cmd.index("-o")
                pattern = cmd[o_idx + 1]
                fake_path = Path(pattern.replace("%(ext)s", "wav"))
                fake_path.write_bytes(b"fake")

        mock_run.side_effect = fake_run

        dest = from_url("https://example.com", "no_ext")
        assert dest.name == "no_ext.wav"

    @patch("voicecli.samples.subprocess.run")
    @patch("voicecli.samples.shutil.which", return_value="/usr/bin/ok")
    def test_no_output_from_ytdlp_raises(self, mock_which, mock_run, tmp_path, monkeypatch):
        samples_dir = tmp_path / "samples"
        samples_dir.mkdir()
        monkeypatch.setattr("voicecli.samples.SAMPLES_DIR", samples_dir)
        monkeypatch.setattr("voicecli.samples.ACTIVE_FILE", samples_dir / ".active")

        # yt-dlp runs but doesn't create any file
        mock_run.return_value = None

        with pytest.raises(RuntimeError, match="did not produce"):
            from_url("https://example.com", "sample")
