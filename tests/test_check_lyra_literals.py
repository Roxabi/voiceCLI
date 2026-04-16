"""Tests for scripts/check-lyra-literals.sh grep-gate."""

import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parent.parent / "scripts" / "check-lyra-literals.sh"


class TestCheckLyraLiterals:
    def test_script_passes_on_clean_tree(self, tmp_path):
        """Script should exit 0 when no lyra. literals exist outside allowlist."""
        # Arrange
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        # Act
        result = subprocess.run(
            [str(SCRIPT)],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        # Assert
        assert result.returncode == 0, f"Script failed on clean tree: {result.stderr}"
        assert "OK:" in result.stdout

    def test_script_fails_on_violator(self, tmp_path):
        """Script should exit 1 when lyra. literal exists outside allowlist."""
        # Arrange
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        violator_file = src_dir / "violator.py"
        violator_file.write_text('SUBJECT = "lyra.voice.generate"\n')

        # Act
        result = subprocess.run(
            [str(SCRIPT)],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        # Assert
        assert result.returncode == 1, (
            f"Script should fail on violator but passed: {result.stdout}"
        )
        assert "ERROR:" in result.stdout
        assert "violator.py" in result.stdout

    def test_script_passes_on_allowlisted_file(self, tmp_path):
        """Script should exit 0 when lyra. literal is in an allowlisted file."""
        # Arrange
        nats_dir = tmp_path / "src" / "voicecli" / "nats"
        nats_dir.mkdir(parents=True)

        stt_adapter = nats_dir / "stt_adapter.py"
        stt_adapter.write_text('SUBJECT = "lyra.voice.stt"\n')

        tts_adapter = nats_dir / "tts_adapter.py"
        tts_adapter.write_text('SUBJECT = "lyra.voice.tts"\n')

        # Act
        result = subprocess.run(
            [str(SCRIPT)],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        # Assert
        assert result.returncode == 0, (
            f"Script failed on allowlisted files: {result.stderr}"
        )
        assert "OK:" in result.stdout

    def test_script_fails_on_multiple_violators(self, tmp_path):
        """Script should report all violators when multiple exist."""
        # Arrange
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        violator1 = src_dir / "file1.py"
        violator1.write_text('SUBJECT = "lyra.voice.generate"\n')

        violator2 = src_dir / "file2.py"
        violator2.write_text('NATS_SUBJECT = "lyra.voice.stt"\n')

        # Act
        result = subprocess.run(
            [str(SCRIPT)],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        # Assert
        assert result.returncode == 1, (
            f"Script should fail on multiple violators but passed: {result.stdout}"
        )
        assert "ERROR:" in result.stdout
        assert "file1.py" in result.stdout
        assert "file2.py" in result.stdout

    def test_mixed_violator_and_allowlisted(self, tmp_path):
        """Script should fail when both violator and allowlisted files exist."""
        # Arrange
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        nats_dir = src_dir / "voicecli" / "nats"
        nats_dir.mkdir(parents=True)

        allowlisted = nats_dir / "tts_adapter.py"
        allowlisted.write_text('SUBJECT = "lyra.voice.tts"\n')

        violator = src_dir / "violator.py"
        violator.write_text('SUBJECT = "lyra.voice.generate"\n')

        # Act
        result = subprocess.run(
            [str(SCRIPT)],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        # Assert
        assert result.returncode == 1, (
            f"Script should fail with mixed files but passed: {result.stdout}"
        )
        assert "ERROR:" in result.stdout
        assert "violator.py" in result.stdout

    def test_stderr_output_on_violator(self, tmp_path):
        """Script should output error details to stdout when violations found."""
        # Arrange
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        violator_file = src_dir / "violator.py"
        violator_file.write_text('SUBJECT = "lyra.voice.generate"\n')

        # Act
        result = subprocess.run(
            [str(SCRIPT)],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

        # Assert
        assert result.returncode == 1
        assert len(result.stdout) > 0, "Expected stdout output on violation"
        assert "lyra." in result.stdout or "lyra.*" in result.stdout
