"""Tests for _vram_cleanup in voicecli.daemon (issue #36, V1).

Strategy:
- Import the real _vram_cleanup function from voicecli.runtime.daemon.
- Mock gc.collect and torch.cuda using unittest.mock.patch to avoid GPU
  dependency and to assert call behaviour.
- Three scenarios: CUDA available, CUDA unavailable, torch import failure.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("torch", reason="torch is opt-in via [stt]/[tts]/[all] extras")

from voicecli.runtime.dictation import handle_transcribe_file


class TestVramCleanup:
    def test_calls_gc_collect(self):
        """_vram_cleanup always calls gc.collect()."""
        # Arrange
        from voicecli.runtime.daemon import _vram_cleanup

        mock_cuda = MagicMock()
        mock_cuda.is_available.return_value = True

        with (
            patch("gc.collect") as mock_gc,
            patch("torch.cuda", mock_cuda),
        ):
            # Act
            _vram_cleanup()

        # Assert
        mock_gc.assert_called_once()

    def test_calls_empty_cache_when_cuda_available(self):
        """_vram_cleanup calls torch.cuda.empty_cache() when CUDA is available."""
        # Arrange
        from voicecli.runtime.daemon import _vram_cleanup

        mock_cuda = MagicMock()
        mock_cuda.is_available.return_value = True

        with (
            patch("gc.collect"),
            patch("torch.cuda", mock_cuda),
        ):
            # Act
            _vram_cleanup()

        # Assert
        mock_cuda.is_available.assert_called_once()
        mock_cuda.empty_cache.assert_called_once()

    def test_does_not_call_empty_cache_when_cuda_unavailable(self):
        """_vram_cleanup skips torch.cuda.empty_cache() when CUDA is unavailable."""
        # Arrange
        from voicecli.runtime.daemon import _vram_cleanup

        mock_cuda = MagicMock()
        mock_cuda.is_available.return_value = False

        with (
            patch("gc.collect"),
            patch("torch.cuda", mock_cuda),
        ):
            # Act
            _vram_cleanup()

        # Assert
        mock_cuda.is_available.assert_called_once()
        mock_cuda.empty_cache.assert_not_called()

    def test_does_not_crash_when_torch_unavailable(self):
        """_vram_cleanup does not raise if torch cannot be imported."""
        # Arrange
        from voicecli.runtime.daemon import _vram_cleanup

        # Simulate ImportError by making the import block raise
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "torch":
                raise ImportError("No module named 'torch'")
            return real_import(name, *args, **kwargs)

        with (
            patch("gc.collect"),
            patch("builtins.__import__", side_effect=mock_import),
        ):
            # Act + Assert — must not raise
            _vram_cleanup()

    def test_does_not_crash_when_torch_raises_on_empty_cache(self):
        """_vram_cleanup suppresses exceptions raised by torch.cuda.empty_cache()."""
        # Arrange
        from voicecli.runtime.daemon import _vram_cleanup

        mock_cuda = MagicMock()
        mock_cuda.is_available.return_value = True
        mock_cuda.empty_cache.side_effect = RuntimeError("CUDA error: device not ready")

        with (
            patch("gc.collect"),
            patch("torch.cuda", mock_cuda),
        ):
            # Act + Assert — must not raise
            _vram_cleanup()


# ---------------------------------------------------------------------------
# T09a — STT lazy warmup: serve() must NOT eagerly call warmup()
# ---------------------------------------------------------------------------


class TestSttLazyWarmup:
    def test_serve_does_not_call_warmup(self, tmp_path):
        """serve() should not eagerly call warmup() — model loads on first request."""
        # Arrange
        from voicecli.runtime.stt_daemon import SttDaemon

        sock_path = tmp_path / "stt-lazy.sock"
        daemon = SttDaemon(socket_path=sock_path)

        # Make the socket raise KeyboardInterrupt immediately after bind so
        # serve() exits without ever entering the accept loop.
        original_socket = __import__("socket").socket

        class _BreakOnListen(original_socket):
            def listen(self, *args, **kwargs):
                raise KeyboardInterrupt("break out of serve() for test")

        with (
            patch("voicecli.runtime.stt_daemon.warmup") as mock_warmup,
            patch("voicecli.runtime.stt_daemon._probe_pyaudio", return_value=False),
            patch("socket.socket", _BreakOnListen),
        ):
            # Act — serve() must exit cleanly via the KeyboardInterrupt path
            try:
                daemon.serve()
            except KeyboardInterrupt:
                pass  # serve() swallows OSError/KeyboardInterrupt; just in case

        # Assert
        mock_warmup.assert_not_called()


# ---------------------------------------------------------------------------
# T09b — STT OOM retry: _handle_transcribe_file retries on OutOfMemoryError
# ---------------------------------------------------------------------------


# Declare a stand-alone OOM exception class so that torch need not be
# importable in the test environment.  The daemon checks:
#   isinstance(e, torch.cuda.OutOfMemoryError)
# so we patch torch.cuda.OutOfMemoryError to point at this class and make
# transcribe raise instances of it.
class _FakeOOM(RuntimeError):
    """Stands in for torch.cuda.OutOfMemoryError (RuntimeError subclass)."""


class TestSttOomRetry:
    """Tests for the OOM retry loop inside _handle_transcribe_file."""

    def _make_daemon_and_conn(self, tmp_path):
        """Return a minimal (SttDaemon, audio_path, fake_conn) tuple for unit tests."""
        from unittest.mock import MagicMock

        from voicecli.runtime.stt_daemon import SttDaemon

        audio_path = tmp_path / "audio.wav"
        audio_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")  # minimal WAV-ish header

        daemon = SttDaemon(model="large-v3-turbo", socket_path=tmp_path / "stt.sock")

        # Fake connection that captures what _send_json writes
        fake_conn = MagicMock()
        fake_conn.sent_data: list[bytes] = []

        def _capture_send(data):
            fake_conn.sent_data.append(data)

        fake_conn.sendall.side_effect = _capture_send

        return daemon, audio_path, fake_conn

    def test_retries_on_oom_then_succeeds(self, tmp_path):
        """Should retry on OutOfMemoryError and succeed on subsequent attempt."""
        # Arrange
        import json

        from unittest.mock import MagicMock

        daemon, audio_path, fake_conn = self._make_daemon_and_conn(tmp_path)

        call_count = {"n": 0}

        def _transcribe_side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise _FakeOOM("CUDA out of memory")
            # Second attempt succeeds
            result = MagicMock()
            result.text = "hello world"
            result.language = "en"
            result.segments = []
            return result

        mock_cuda = MagicMock()
        mock_cuda.OutOfMemoryError = _FakeOOM

        with (
            patch("voicecli.runtime.stt_daemon.load_stt_config", return_value={}),
            patch("voicecli.runtime.transcribe.transcribe", side_effect=_transcribe_side_effect),
            patch("time.sleep"),
            patch("gc.collect"),
            patch("torch.cuda", mock_cuda),
            patch("torch.cuda.empty_cache"),
        ):
            req = {"action": "transcribe_file", "audio_path": str(audio_path)}

            # Act
            handle_transcribe_file(daemon, fake_conn, req)

        # Assert — exactly 2 transcribe calls (1 OOM + 1 success)
        assert call_count["n"] == 2

        # Response must be status=ok
        assert fake_conn.sendall.call_count >= 1
        sent_bytes = fake_conn.sendall.call_args[0][0]
        response = json.loads(sent_bytes.decode().rstrip("\n"))
        assert response["status"] == "ok"
        assert response["text"] == "hello world"

    def test_returns_error_after_max_retries(self, tmp_path):
        """Should return error response after 3 OOM failures."""
        # Arrange
        import json

        daemon, audio_path, fake_conn = self._make_daemon_and_conn(tmp_path)

        def _always_oom(*args, **kwargs):
            raise _FakeOOM("CUDA out of memory")

        mock_cuda = MagicMock()
        mock_cuda.OutOfMemoryError = _FakeOOM

        with (
            patch("voicecli.runtime.stt_daemon.load_stt_config", return_value={}),
            patch("voicecli.runtime.transcribe.transcribe", side_effect=_always_oom),
            patch("time.sleep") as mock_sleep,
            patch("gc.collect"),
            patch("torch.cuda", mock_cuda),
            patch("torch.cuda.empty_cache"),
        ):
            req = {"action": "transcribe_file", "audio_path": str(audio_path)}

            # Act
            handle_transcribe_file(daemon, fake_conn, req)

        # Assert — daemon sends one final error response after exhausting retries
        assert fake_conn.sendall.call_count >= 1
        sent_bytes = fake_conn.sendall.call_args[0][0]
        response = json.loads(sent_bytes.decode().rstrip("\n"))
        assert response["status"] == "error"
        assert "OOM" in response["message"] or "retries" in response["message"]

        # Assert — backoff timing: 5s → 10s → 20s
        from unittest.mock import call

        mock_sleep.assert_has_calls([call(5), call(10), call(20)])

    def test_non_oom_exception_returns_immediately(self, tmp_path):
        """Non-OOM exceptions should return an error immediately without retry."""
        # Arrange
        import json

        daemon, audio_path, fake_conn = self._make_daemon_and_conn(tmp_path)

        mock_cuda = MagicMock()
        mock_cuda.OutOfMemoryError = _FakeOOM

        with (
            patch("voicecli.runtime.stt_daemon.load_stt_config", return_value={}),
            patch(
                "voicecli.runtime.transcribe.transcribe",
                side_effect=ValueError("Unknown model 'bad'"),
            ),
            patch("time.sleep") as mock_sleep,
            patch("gc.collect"),
            patch("torch.cuda", mock_cuda),
            patch("torch.cuda.empty_cache"),
        ):
            req = {"action": "transcribe_file", "audio_path": str(audio_path)}

            # Act
            handle_transcribe_file(daemon, fake_conn, req)

        # Assert — error returned immediately, no sleep/retry
        assert fake_conn.sendall.call_count >= 1
        sent_bytes = fake_conn.sendall.call_args[0][0]
        response = json.loads(sent_bytes.decode().rstrip("\n"))
        assert response["status"] == "error"
        assert "bad" in response["message"]
        mock_sleep.assert_not_called()
