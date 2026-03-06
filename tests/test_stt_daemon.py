"""Tests for voicecli.stt_daemon — RED phase.

The implementation (src/voicecli/stt_daemon.py) does not exist yet.
All tests are expected to fail with ImportError until the GREEN phase.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from voicecli.transcribe import TranscriptionResult


# ---------------------------------------------------------------------------
# Module-level mocks applied before SttDaemon is imported in fixtures.
# We define the mock objects here so tests can inspect call counts / args.
# ---------------------------------------------------------------------------

_MOCK_TRANSCRIPTION = TranscriptionResult(text="hello world", language="en", segments=[])


def _make_mock_recording_thread():
    """Return a MagicMock that acts like RecordingThread."""
    mock = MagicMock()
    # .stop() returns empty WAV-like bytes immediately
    mock.stop.return_value = b"RIFF\x00\x00\x00\x00WAVEfmt "
    mock.start.return_value = None
    return mock


# ---------------------------------------------------------------------------
# Fixture: daemon_send
# ---------------------------------------------------------------------------


@pytest.fixture()
def daemon_send(tmp_path):
    """Start an SttDaemon in a background thread and yield a send() helper.

    All heavy externals are mocked:
      - _probe_pyaudio   → always returns True
      - RecordingThread  → mock that returns empty wav bytes on .stop()
      - _chime           → no-op
      - _write_clipboard → no-op (captured for assertions)
      - transcribe       → returns TranscriptionResult("hello world", "en", [])
      - warmup           → no-op
    """
    sock_path = tmp_path / "stt-test.sock"

    # Build fresh mock objects per fixture invocation so call counts are clean.
    mock_recording_thread_cls = MagicMock(side_effect=_make_mock_recording_thread)
    mock_chime = MagicMock()
    mock_write_clipboard = MagicMock()
    mock_warmup = MagicMock()

    with (
        patch("voicecli.stt_daemon._probe_pyaudio", return_value=True),
        patch("voicecli.stt_daemon.RecordingThread", mock_recording_thread_cls),
        patch("voicecli.stt_daemon._chime", mock_chime),
        patch("voicecli.stt_daemon._write_clipboard", mock_write_clipboard),
        patch("voicecli.stt_daemon.warmup", mock_warmup),
        patch("voicecli.transcribe.transcribe", return_value=_MOCK_TRANSCRIPTION),
    ):
        from voicecli.stt_daemon import SttDaemon, SOCKET_PATH as _DEFAULT_SOCKET_PATH

        daemon = SttDaemon(model="large-v3-turbo", socket_path=sock_path)
        t = threading.Thread(target=daemon.serve, daemon=True)
        t.start()

        # Wait for the socket to appear (up to 3 s).
        deadline = time.monotonic() + 3.0
        while not sock_path.exists():
            if time.monotonic() > deadline:
                raise RuntimeError(f"Daemon socket never appeared at {sock_path}")
            time.sleep(0.02)

        def send(action: str, **kwargs) -> dict:
            """Connect to the daemon socket, send one action, return parsed response."""
            payload = {"action": action, **kwargs}
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect(str(sock_path))
            try:
                sock.sendall((json.dumps(payload) + "\n").encode())
                buf = bytearray()
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    if b"\n" in buf:
                        break
                return json.loads(buf.split(b"\n")[0])
            finally:
                sock.close()

        yield send, mock_chime, mock_write_clipboard

        # Tear down: stop the accept loop.
        daemon.stop()
        t.join(timeout=2.0)


# ---------------------------------------------------------------------------
# S1 — Socket skeleton
# ---------------------------------------------------------------------------


class TestSocketSkeleton:
    def test_ping(self, daemon_send):
        """N1: ping → {"status": "ok"}."""
        send, _, _ = daemon_send
        # Arrange / Act
        resp = send("ping")
        # Assert
        assert resp == {"status": "ok"}

    def test_status_idle(self, daemon_send):
        """N2: status in idle state → {"status":"ok","state":"idle"}."""
        send, _, _ = daemon_send
        # Arrange / Act
        resp = send("status")
        # Assert
        assert resp["status"] == "ok"
        assert resp["state"] == "idle"

    def test_unknown_action(self, daemon_send):
        """N7: unknown action → {"status":"error","message":"unknown action: foo"}."""
        send, _, _ = daemon_send
        # Arrange / Act
        resp = send("foo")
        # Assert
        assert resp["status"] == "error"
        assert resp["message"] == "unknown action: foo"

    def test_mode_field_ignored(self, daemon_send):
        """Forward-compat: mode field on toggle must not crash; response is valid JSON."""
        send, _, _ = daemon_send
        # Arrange / Act — toggle from idle with a mode field
        resp = send("toggle", mode={"language": "fr"})
        # Assert — any valid JSON response with a "status" key is acceptable
        assert "status" in resp


# ---------------------------------------------------------------------------
# S2 — Recording + chimes
# ---------------------------------------------------------------------------


class TestRecordingAndChimes:
    def test_toggle_starts_recording(self, daemon_send):
        """N3: toggle from idle → {"status":"ok","state":"recording"}."""
        send, _, _ = daemon_send
        # Arrange / Act
        resp = send("toggle")
        # Assert
        assert resp["status"] == "ok"
        assert resp["state"] == "recording"

    def test_status_during_recording(self, daemon_send):
        """status after N3 → state=recording."""
        send, _, _ = daemon_send
        # Arrange
        send("toggle")  # N3: idle → recording
        # Act
        resp = send("status")
        # Assert
        assert resp["state"] == "recording"

    def test_toggle_stops_recording(self, daemon_send):
        """N3 then N4: second toggle → state=idle."""
        send, _, _ = daemon_send
        # Arrange
        send("toggle")  # N3: idle → recording
        # Act
        resp = send("toggle")  # N4: recording → idle (no real transcription needed here)
        # Assert
        assert resp["status"] == "ok"
        assert resp["state"] == "idle"

    def test_chime_start_called(self, daemon_send):
        """_chime("start") is called when N3 fires."""
        send, mock_chime, _ = daemon_send
        # Arrange / Act
        send("toggle")  # N3
        # Small wait for the chime thread to execute
        time.sleep(0.1)
        # Assert
        mock_chime.assert_any_call("start")

    def test_chime_stop_called(self, daemon_send):
        """_chime("stop") is called after N4 completes."""
        send, mock_chime, _ = daemon_send
        # Arrange
        send("toggle")  # N3
        # Act
        send("toggle")  # N4 — blocks until transcription done, chime fires inside
        # Small wait for the chime thread to execute
        time.sleep(0.1)
        # Assert
        mock_chime.assert_any_call("stop")


# ---------------------------------------------------------------------------
# S3 — Transcription + clipboard
# ---------------------------------------------------------------------------


class TestTranscriptionAndClipboard:
    def test_transcription_result_in_response(self, daemon_send):
        """N4 response includes text and language from TranscriptionResult."""
        send, _, _ = daemon_send
        # Arrange
        send("toggle")  # N3: start recording
        # Act
        resp = send("toggle")  # N4: stop + transcribe
        # Assert
        assert resp["status"] == "ok"
        assert resp["text"] == "hello world"
        assert resp["language"] == "en"

    def test_clipboard_called(self, daemon_send):
        """_write_clipboard is called with the transcribed text."""
        send, _, mock_write_clipboard = daemon_send
        # Arrange
        send("toggle")  # N3
        # Act
        send("toggle")  # N4
        # Assert
        mock_write_clipboard.assert_called_once_with("hello world")

    def test_tempfile_deleted(self, daemon_send, tmp_path, monkeypatch):
        """Tempfile created during N4 is deleted after transcription."""
        send, _, _ = daemon_send

        created_paths: list[Path] = []

        # Wrap _write_tempfile to capture the path it creates.
        import voicecli.stt_daemon as stt_mod

        original_write_tempfile = stt_mod._write_tempfile

        def tracking_write_tempfile(wav_bytes: bytes) -> Path:
            p = original_write_tempfile(wav_bytes)
            created_paths.append(p)
            return p

        monkeypatch.setattr(stt_mod, "_write_tempfile", tracking_write_tempfile)

        # Arrange
        send("toggle")  # N3
        # Act
        send("toggle")  # N4
        # Assert — the file that was created must no longer exist
        assert len(created_paths) == 1
        assert not created_paths[0].exists(), (
            f"Tempfile {created_paths[0]} was not deleted after transcription"
        )

    def test_tempfile_deleted_on_error(self, daemon_send, monkeypatch):
        """Tempfile is deleted even when transcribe() raises an exception."""
        send, _, _ = daemon_send

        import voicecli.stt_daemon as stt_mod
        import voicecli.transcribe as transcribe_mod

        created_paths: list[Path] = []
        original_write_tempfile = stt_mod._write_tempfile

        def tracking_write_tempfile(wav_bytes: bytes) -> Path:
            p = original_write_tempfile(wav_bytes)
            created_paths.append(p)
            return p

        monkeypatch.setattr(stt_mod, "_write_tempfile", tracking_write_tempfile)
        monkeypatch.setattr(
            transcribe_mod,
            "transcribe",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        # Arrange
        send("toggle")  # N3
        # Act — N4 will call the failing transcribe
        resp = send("toggle")
        # Assert — file cleaned up, response still valid
        assert len(created_paths) == 1
        assert not created_paths[0].exists(), (
            f"Tempfile {created_paths[0]} was not deleted after transcription error"
        )
        # The daemon should respond gracefully (not crash)
        assert "status" in resp


# ---------------------------------------------------------------------------
# S4 — Queue support
# ---------------------------------------------------------------------------


class TestQueueSupport:
    """Tests for the queued state (N5/N6 and auto-restart after transcription)."""

    def _make_blocking_transcribe(self):
        """Return a (transcribe_fn, unblock_event) pair.

        transcribe_fn blocks until unblock_event is set.
        """
        unblock = threading.Event()
        call_started = threading.Event()

        def blocking_transcribe(*args, **kwargs):
            call_started.set()
            unblock.wait(timeout=5.0)
            return _MOCK_TRANSCRIPTION

        return blocking_transcribe, unblock, call_started

    def test_queue_during_transcribing(self, daemon_send, monkeypatch):
        """N5: toggle during transcribing → {"status":"ok","state":"queued"} immediately."""
        send, _, _ = daemon_send

        blocking_transcribe, unblock, call_started = self._make_blocking_transcribe()

        import voicecli.transcribe as transcribe_mod

        monkeypatch.setattr(transcribe_mod, "transcribe", blocking_transcribe)

        # Arrange: start recording
        send("toggle")  # N3: idle → recording

        # Kick off N4 in a background thread (it will block on transcription).
        n4_result: dict = {}

        def do_n4():
            n4_result["resp"] = send("toggle")  # N4: recording → transcribing (blocks)

        n4_thread = threading.Thread(target=do_n4, daemon=True)
        n4_thread.start()

        # Wait until the blocking transcribe has started so we know state=transcribing.
        assert call_started.wait(timeout=3.0), "blocking_transcribe never started"

        # Act: send toggle during transcribing (N5) — must respond immediately.
        t_before = time.monotonic()
        resp = send("toggle")
        elapsed = time.monotonic() - t_before

        # Assert: immediate queued response
        assert resp["status"] == "ok"
        assert resp["state"] == "queued"
        assert elapsed < 0.5, f"N5 response took {elapsed:.2f}s (expected < 0.5s)"

        # Cleanup: unblock N4 thread
        unblock.set()
        n4_thread.join(timeout=3.0)

    def test_queue_idempotent(self, daemon_send, monkeypatch):
        """N6: second toggle while already queued → {"status":"ok","state":"queued"}."""
        send, _, _ = daemon_send

        blocking_transcribe, unblock, call_started = self._make_blocking_transcribe()

        import voicecli.transcribe as transcribe_mod

        monkeypatch.setattr(transcribe_mod, "transcribe", blocking_transcribe)

        # Arrange: idle → recording → transcribing → queued
        send("toggle")  # N3

        n4_thread = threading.Thread(target=lambda: send("toggle"), daemon=True)
        n4_thread.start()
        assert call_started.wait(timeout=3.0)

        send("toggle")  # N5: → queued

        # Act: another toggle while queued (N6)
        resp = send("toggle")

        # Assert
        assert resp["status"] == "ok"
        assert resp["state"] == "queued"

        # Cleanup
        unblock.set()
        n4_thread.join(timeout=3.0)

    def test_auto_start_after_queue(self, daemon_send, monkeypatch):
        """After transcription completes with queued state, state becomes recording."""
        send, _, _ = daemon_send

        blocking_transcribe, unblock, call_started = self._make_blocking_transcribe()

        import voicecli.transcribe as transcribe_mod

        monkeypatch.setattr(transcribe_mod, "transcribe", blocking_transcribe)

        # Arrange: idle → recording
        send("toggle")  # N3

        n4_thread = threading.Thread(target=lambda: send("toggle"), daemon=True)
        n4_thread.start()
        assert call_started.wait(timeout=3.0)

        # Queue a new recording
        send("toggle")  # N5: → queued

        # Act: let transcription finish — auto-start should kick in
        unblock.set()
        n4_thread.join(timeout=3.0)

        # Give the daemon a moment to transition into recording
        time.sleep(0.15)

        # Assert: state should now be recording (auto-started)
        resp = send("status")
        assert resp["state"] == "recording", (
            f"Expected state=recording after queued auto-start, got {resp['state']!r}"
        )

        # Cleanup: stop the auto-started recording
        # Use a new non-blocking transcribe for cleanup
        import voicecli.stt_daemon as stt_mod

        monkeypatch.setattr(transcribe_mod, "transcribe", lambda *a, **kw: _MOCK_TRANSCRIPTION)
        send("toggle")  # N4 on the auto-started recording
