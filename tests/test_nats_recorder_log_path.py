"""Test that start_recording surfaces the log path when the recorder fails to start.

Covers the branch where the spawned subprocess exits immediately (e.g. due to an
ImportError) and never writes the state file within the poll window.  The returned
error dict must include the RECORDER_LOG path so the caller — and the wrapper —
can surface it to the user.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voicecli import nats_recorder


def test_start_recording_error_includes_log_path(tmp_path: Path) -> None:
    """When the recorder subprocess fails to write the state file, the error
    message must include the RECORDER_LOG path."""

    # Arrange: patch STATE_DIR so no real filesystem state is touched, and
    # override the poll loop to skip sleeping by making STATE_FILE never appear.
    fake_state_dir = tmp_path / "share" / "voicecli"
    fake_state_file = fake_state_dir / "nats-recording.json"
    fake_log_dir = tmp_path / "state" / "voicecli"

    fake_proc = MagicMock(spec=subprocess.Popen)
    fake_proc.pid = 99999

    with (
        patch.object(nats_recorder, "STATE_DIR", fake_state_dir),
        patch.object(nats_recorder, "STATE_FILE", fake_state_file),
        patch.object(nats_recorder, "LOG_DIR", fake_log_dir),
        patch.object(nats_recorder, "RECORDER_LOG", fake_log_dir / "recorder.log"),
        patch.object(nats_recorder, "is_recording", return_value=False),
        patch("subprocess.Popen", return_value=fake_proc),
        patch("time.sleep"),  # skip the 100 ms poll sleeps
    ):
        # Act: state file never gets written → poll loop exhausts → error
        result = nats_recorder.start_recording(model="large-v3-turbo")

    # Assert
    assert "error" in result
    assert str(fake_log_dir / "recorder.log") in result["error"], (
        f"Expected log path in error message, got: {result['error']!r}"
    )
