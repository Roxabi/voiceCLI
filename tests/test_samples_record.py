"""Tests for samples record command and record_sample()."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voicecli.samples import _check_tool, record_sample


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def samples_env(tmp_path, monkeypatch):
    """Temporary samples dir with patched SAMPLES_DIR."""
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    monkeypatch.setattr("voicecli.samples.SAMPLES_DIR", samples_dir)
    # soundfile.info patched so happy-path fixtures need not produce real WAV bytes
    monkeypatch.setattr("soundfile.info", lambda _: None)
    return samples_dir


@pytest.fixture()
def mock_chime(monkeypatch):
    monkeypatch.setattr("voicecli.samples._chime", lambda *_: None)


# ── _check_tool — parecord hint ───────────────────────────────────────────────


class TestCheckToolParecord:
    def test_not_found_parecord(self):
        with patch("voicecli.samples.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="parecord.*not found"):
                _check_tool("parecord")

    def test_not_found_parecord_hint(self):
        with patch("voicecli.samples.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="pulseaudio-utils"):
                _check_tool("parecord")


# ── record_sample unit tests ─────────────────────────────────────────────────


class TestRecordSample:
    @patch("voicecli.samples.shutil.which", return_value="/usr/bin/parecord")
    def test_happy_path_timeout_is_normal_stop(self, _mock_which, samples_env, mock_chime):
        # Arrange — parecord times out (normal), dest was written during recording
        def fake_run(cmd, *, timeout):
            dest = Path(cmd[-1])
            dest.write_bytes(b"fake wav data")
            raise subprocess.TimeoutExpired(cmd, timeout)

        with patch("voicecli.samples.subprocess.run", side_effect=fake_run):
            # Act
            result = record_sample("myrecording", duration=5.0)

        # Assert
        assert result == samples_env / "myrecording.wav"
        assert result.exists()

    @patch("voicecli.samples.shutil.which", return_value="/usr/bin/parecord")
    def test_happy_path_appends_wav_extension(self, _mock_which, samples_env, mock_chime):
        def fake_run(cmd, *, timeout):
            Path(cmd[-1]).write_bytes(b"data")
            raise subprocess.TimeoutExpired(cmd, timeout)

        with patch("voicecli.samples.subprocess.run", side_effect=fake_run):
            result = record_sample("noext", duration=2.0)

        assert result.name == "noext.wav"

    @patch("voicecli.samples.shutil.which", return_value="/usr/bin/parecord")
    def test_corrupt_wav_raises(self, _mock_which, samples_env, mock_chime, monkeypatch):
        import soundfile

        def fake_run(cmd, *, timeout):
            Path(cmd[-1]).write_bytes(b"not a wav")
            raise subprocess.TimeoutExpired(cmd, timeout)

        def corrupt_info(_):
            raise soundfile.LibsndfileError("fake", 0)

        monkeypatch.setattr("soundfile.info", corrupt_info)
        dest = samples_env / "corrupt.wav"

        with patch("voicecli.samples.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="recorded file appears corrupt"):
                record_sample("corrupt", duration=2.0)

        assert not dest.exists()

    @patch("voicecli.samples.shutil.which", return_value=None)
    def test_missing_parecord_raises(self, _mock_which):
        with pytest.raises(RuntimeError, match="parecord.*not found"):
            record_sample("test")

    @patch("voicecli.samples.shutil.which", return_value="/usr/bin/parecord")
    def test_nonzero_returncode_raises(self, _mock_which, samples_env, mock_chime):
        # Arrange — parecord exits non-zero (PulseAudio not running etc.)
        mock_result = MagicMock()
        mock_result.returncode = 1
        dest = samples_env / "test.wav"
        dest.write_bytes(b"partial")  # file exists so we reach returncode check

        with patch("voicecli.samples.subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="parecord failed \\(exit 1\\)"):
                record_sample("test", duration=2.0)

    @patch("voicecli.samples.shutil.which", return_value="/usr/bin/parecord")
    def test_dest_missing_after_timeout_raises(self, _mock_which, samples_env, mock_chime):
        # Arrange — timeout but dest never written
        def fake_run(cmd, *, timeout):
            raise subprocess.TimeoutExpired(cmd, timeout)  # no file written

        with patch("voicecli.samples.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="parecord produced no output"):
                record_sample("test", duration=2.0)

    @patch("voicecli.samples.shutil.which", return_value="/usr/bin/parecord")
    def test_zero_byte_dest_raises(self, _mock_which, samples_env, mock_chime):
        # Arrange — timeout, dest is zero bytes (empty file written)
        def fake_run(cmd, *, timeout):
            Path(cmd[-1]).touch()  # zero-byte file
            raise subprocess.TimeoutExpired(cmd, timeout)

        with patch("voicecli.samples.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="parecord produced no output"):
                record_sample("test", duration=2.0)

    def test_negative_duration_raises(self):
        with pytest.raises(ValueError, match="duration must be positive"):
            record_sample("test", duration=-1.0)

    def test_zero_duration_raises(self):
        with pytest.raises(ValueError, match="duration must be positive"):
            record_sample("test", duration=0)


# ── CLI command tests ────────────────────────────────────────────────────────


class TestSamplesRecordCommand:
    def test_happy_path(self):
        from typer.testing import CliRunner

        from voicecli.cli import app

        runner = CliRunner()
        fake_dest = Path("TTS/samples/mymic.wav")

        with patch("voicecli.samples.record_sample", return_value=fake_dest):
            result = runner.invoke(app, ["samples", "record", "mymic"])

        assert result.exit_code == 0
        assert "Recorded" in result.output

    def test_runtime_error_exits_1(self):
        from typer.testing import CliRunner

        from voicecli.cli import app

        runner = CliRunner()

        with patch(
            "voicecli.samples.record_sample",
            side_effect=RuntimeError("parecord not found on PATH"),
        ):
            result = runner.invoke(app, ["samples", "record", "mymic"])

        assert result.exit_code == 1

    def test_os_error_exits_1(self):
        from typer.testing import CliRunner

        from voicecli.cli import app

        runner = CliRunner()

        with patch(
            "voicecli.samples.record_sample",
            side_effect=OSError("device busy"),
        ):
            result = runner.invoke(app, ["samples", "record", "mymic"])

        assert result.exit_code == 1


# ── from-url ValueError prefix (new in this PR) ───────────────────────────────


class TestFromUrlValueErrorPrefix:
    def test_value_error_uses_invalid_argument_prefix(self):
        from typer.testing import CliRunner

        from voicecli.cli import app

        runner = CliRunner()

        with patch(
            "voicecli.samples.from_url",
            side_effect=ValueError("Only http/https URLs are supported"),
        ):
            result = runner.invoke(app, ["samples", "from-url", "file:///etc/passwd", "evil"])

        assert result.exit_code == 1
        assert "Invalid argument:" in result.output
