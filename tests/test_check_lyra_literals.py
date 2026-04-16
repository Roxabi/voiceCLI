"""Tests for scripts/check-lyra-literals.sh grep-gate."""

import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parent.parent / "scripts" / "check-lyra-literals.sh"


def test_script_passes_on_clean_tree():
    """Script should exit 0 when no lyra. literals exist outside allowlist."""
    result = subprocess.run(
        [str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    assert result.returncode == 0, f"Script failed on clean tree: {result.stderr}"
    assert "OK:" in result.stdout


def test_script_fails_on_violator(tmp_path):
    """Script should exit 1 when lyra. literal exists outside allowlist."""
    # Create a temporary src directory with a violator file
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    violator_file = src_dir / "violator.py"
    violator_file.write_text('SUBJECT = "lyra.voice.generate"\n')

    # Run script from tmp_path (override cwd)
    result = subprocess.run(
        [str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    assert result.returncode == 1, f"Script should fail on violator but passed: {result.stdout}"
    assert "ERROR:" in result.stdout
    assert "violator.py" in result.stdout


def test_script_passes_on_allowlisted_file(tmp_path):
    """Script should exit 0 when lyra. literal is in an allowlisted file."""
    # Create allowlisted directories and files
    nats_dir = tmp_path / "src" / "voicecli" / "nats"
    nats_dir.mkdir(parents=True)

    # Create both allowlisted files
    stt_adapter = nats_dir / "stt_adapter.py"
    stt_adapter.write_text('SUBJECT = "lyra.voice.stt"\n')

    tts_adapter = nats_dir / "tts_adapter.py"
    tts_adapter.write_text('SUBJECT = "lyra.voice.tts"\n')

    result = subprocess.run(
        [str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    assert result.returncode == 0, f"Script failed on allowlisted files: {result.stderr}"
    assert "OK:" in result.stdout
