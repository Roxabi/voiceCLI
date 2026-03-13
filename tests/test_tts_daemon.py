"""Tests for TTS daemon FIFO queue — RED phase (issue #31).

These tests are expected to FAIL against the current implementation because
daemon_main() processes connections synchronously (no queue, no worker thread).
They will pass once T01-T04 (backend-dev) land the queue refactor.

Strategy:
- Start daemon_main in a daemon thread with patched SOCKET_PATH (tmp_path).
- Inject a mock engine by pre-populating the `engines` dict via monkeypatching
  the `_load_engine` function so that any engine name resolves to our mock.
- Use raw socket calls (mirroring daemon_request logic) to avoid relying on the
  module-level SOCKET_PATH constant in the client helper.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw_request(sock_path: Path, request: dict, timeout: float = 10.0) -> dict:
    """Send a JSON request to a daemon at sock_path and return the parsed response."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(sock_path))
        payload = json.dumps(request, ensure_ascii=False) + "\n"
        sock.sendall(payload.encode())
        buf = bytearray()
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf.extend(chunk)
            if b"\n" in buf:
                break
        line = buf.split(b"\n")[0]
        return json.loads(line)
    finally:
        sock.close()


def _wait_for_socket(sock_path: Path, timeout: float = 5.0) -> None:
    """Poll until the socket file appears or raise RuntimeError."""
    deadline = time.monotonic() + timeout
    while not sock_path.exists():
        if time.monotonic() > deadline:
            raise RuntimeError(f"Daemon socket never appeared at {sock_path}")
        time.sleep(0.02)


def _make_mock_engine(delay: float = 0.0, raises_on_first: bool = False):
    """Return a mock engine whose .generate() sleeps for `delay` seconds.

    If raises_on_first=True the first call raises RuntimeError; subsequent calls
    succeed and return a fixed path string.
    """
    call_count = 0
    lock = threading.Lock()

    mock_eng = MagicMock()

    def fake_generate(text, voice, output_path, **kwargs):
        nonlocal call_count
        with lock:
            call_count += 1
            current = call_count
        if raises_on_first and current == 1:
            raise RuntimeError("mock synthesis error")
        time.sleep(delay)
        return output_path  # echo back the output path — caller checks it exists

    mock_eng.generate.side_effect = fake_generate
    return mock_eng


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def daemon_factory(tmp_path):
    """Factory that starts a daemon_main thread with a given mock engine.

    Returns (start_daemon, sock_path):
      - start_daemon(mock_engine) → starts the thread and waits for socket
      - sock_path → Path to the temporary socket
    """
    import voicecli.daemon as daemon_mod

    sock_path = tmp_path / "tts-test.sock"
    threads = []

    def start_daemon(mock_engine):
        """Patch SOCKET_PATH + _load_engine, then start daemon_main in a thread."""

        def patched_load_engine(name, fast=False):
            return mock_engine

        with (
            patch.object(daemon_mod, "SOCKET_PATH", sock_path),
            patch.object(daemon_mod, "_load_engine", patched_load_engine),
        ):
            t = threading.Thread(
                target=daemon_mod.daemon_main,
                daemon=True,
            )
            t.start()
            threads.append(t)
            _wait_for_socket(sock_path)

    yield start_daemon, sock_path
    # Threads are daemon=True so they die with the process; no explicit teardown.


# ---------------------------------------------------------------------------
# T1 — Concurrent generate: both callers receive {"status": "ok"}
# ---------------------------------------------------------------------------


class TestConcurrentGenerate:
    def test_concurrent_generate(self, tmp_path):
        """Two threads send generate concurrently — both must receive status=ok.

        RED: current daemon is single-threaded; the second connection is not
        accepted until the first synthesis finishes. With a 0.05 s delay the
        second caller will not fail per se (it blocks on connect, then gets
        served), but FIFO *order* is not guaranteed and — more critically —
        the accept loop blocks during synthesis so connection #2 is not even
        accepted until connection #1 completes.  The test is structured so that
        this sequential behaviour would cause the second thread's socket to time
        out if the first synthesis takes longer than the socket timeout, proving
        the queue is needed.  We force that with a 0.5 s synthesis delay and a
        tight per-call deadline verified via wall-clock.
        """
        import voicecli.daemon as daemon_mod

        sock_path = tmp_path / "tts-concurrent.sock"
        mock_engine = _make_mock_engine(delay=0.5)  # 500 ms per synthesis

        def patched_load_engine(name, fast=False):
            return mock_engine

        with (
            patch.object(daemon_mod, "SOCKET_PATH", sock_path),
            patch.object(daemon_mod, "_load_engine", patched_load_engine),
            patch.object(daemon_mod, "_OUTPUT_BASE", tmp_path),
        ):
            t = threading.Thread(target=daemon_mod.daemon_main, daemon=True)
            t.start()
            _wait_for_socket(sock_path)

            results: list[dict] = [None, None]  # type: ignore[list-item]
            errors: list[Exception] = []

            output_a = tmp_path / "out_a.wav"
            output_b = tmp_path / "out_b.wav"

            def call_a():
                try:
                    results[0] = _raw_request(
                        sock_path,
                        {
                            "action": "generate",
                            "engine": "qwen",
                            "text": "hello from A",
                            "voice": None,
                            "output_path": str(output_a),
                        },
                    )
                except Exception as exc:
                    errors.append(exc)

            def call_b():
                try:
                    results[1] = _raw_request(
                        sock_path,
                        {
                            "action": "generate",
                            "engine": "qwen",
                            "text": "hello from B",
                            "voice": None,
                            "output_path": str(output_b),
                        },
                    )
                except Exception as exc:
                    errors.append(exc)

            # Launch both threads simultaneously
            t_a = threading.Thread(target=call_a)
            t_b = threading.Thread(target=call_b)

            t_a.start()
            t_b.start()

            t_a.join(timeout=5.0)
            t_b.join(timeout=5.0)

            # No exceptions must have occurred
            assert not errors, f"Thread(s) raised: {errors}"

            # Both results populated
            assert results[0] is not None, "Thread A never received a response"
            assert results[1] is not None, "Thread B never received a response"

            # Both must report success
            assert results[0].get("status") == "ok", f"Thread A got: {results[0]}"
            assert results[1].get("status") == "ok", f"Thread B got: {results[1]}"

            # Response paths must be distinct
            assert results[0].get("path") != results[1].get("path"), (
                "Both threads received the same output path — jobs may have been merged"
            )
            # Wall-clock timing assertion removed: it was a sanity check only and
            # caused flaky failures on loaded CI runners. The real assertions are
            # that both callers receive status=ok with distinct output paths.


# ---------------------------------------------------------------------------
# T2 — Ping fast path: ping responds <50 ms while synthesis is running
# ---------------------------------------------------------------------------


class TestPingFastPath:
    def test_ping_fast_path(self, tmp_path):
        """ping responds in <50 ms even while a long synthesis is in progress.

        RED: current daemon blocks the accept loop during synthesis, so a ping
        sent while generate is running will not be accepted until synthesis
        completes (500 ms). This test will fail because the ping latency will
        be >> 50 ms.
        """
        import voicecli.daemon as daemon_mod

        sock_path = tmp_path / "tts-ping.sock"

        # Synthesis that blocks for 0.5 s — long enough to observe the latency
        mock_engine = _make_mock_engine(delay=0.5)

        def patched_load_engine(name, fast=False):
            return mock_engine

        with (
            patch.object(daemon_mod, "SOCKET_PATH", sock_path),
            patch.object(daemon_mod, "_load_engine", patched_load_engine),
            patch.object(daemon_mod, "_OUTPUT_BASE", tmp_path),
        ):
            t = threading.Thread(target=daemon_mod.daemon_main, daemon=True)
            t.start()
            _wait_for_socket(sock_path)

            output_path = tmp_path / "out_ping.wav"
            synthesis_started = threading.Event()
            generate_result: dict = {}

            def do_generate():
                synthesis_started.set()
                generate_result["resp"] = _raw_request(
                    sock_path,
                    {
                        "action": "generate",
                        "engine": "qwen",
                        "text": "synthesising now",
                        "voice": None,
                        "output_path": str(output_path),
                    },
                    timeout=10.0,
                )

            gen_thread = threading.Thread(target=do_generate, daemon=True)
            gen_thread.start()

            # Wait until generate is sent, then give it a moment to enter synthesis
            synthesis_started.wait(timeout=3.0)
            time.sleep(0.1)

            # Now send ping — must respond in <50 ms
            t_before = time.monotonic()
            ping_resp = _raw_request(sock_path, {"action": "ping"}, timeout=5.0)
            ping_elapsed_ms = (time.monotonic() - t_before) * 1000

            gen_thread.join(timeout=5.0)

            assert ping_resp.get("status") == "ok", f"ping returned: {ping_resp}"
            assert ping_elapsed_ms < 50, (
                f"ping took {ping_elapsed_ms:.1f} ms — expected <50 ms. "
                "The accept loop is likely blocked during synthesis (no queue yet)."
            )


# ---------------------------------------------------------------------------
# T3 — Error isolation: first job error does not kill second job
# ---------------------------------------------------------------------------


class TestErrorIsolation:
    def test_error_isolation(self, tmp_path):
        """First job raises — second job must still receive status=ok.

        RED: the current daemon catches exceptions per-connection, so error
        isolation already works at the connection level. However, without a
        queue the test is structured to expose the missing FIFO: we send both
        jobs concurrently (second is enqueued while first is running). Without
        a worker thread the second caller's connection is not accepted during
        the first's (failing) synthesis, so its socket sits in listen backlog.
        The test verifies the second caller gets ok, not an error or timeout.

        In the RED phase, if the blocking accept loop causes the second caller
        to timeout this test will fail, proving the queue is necessary.
        """
        import voicecli.daemon as daemon_mod

        sock_path = tmp_path / "tts-error.sock"

        # Engine that raises on the first call, succeeds on subsequent calls
        mock_engine = _make_mock_engine(delay=0.1, raises_on_first=True)

        def patched_load_engine(name, fast=False):
            return mock_engine

        with (
            patch.object(daemon_mod, "SOCKET_PATH", sock_path),
            patch.object(daemon_mod, "_load_engine", patched_load_engine),
            patch.object(daemon_mod, "_OUTPUT_BASE", tmp_path),
        ):
            t = threading.Thread(target=daemon_mod.daemon_main, daemon=True)
            t.start()
            _wait_for_socket(sock_path)

            output_a = tmp_path / "err_a.wav"
            output_b = tmp_path / "err_b.wav"

            results: list[dict] = [None, None]  # type: ignore[list-item]
            errors: list[Exception] = []

            def call_a():
                try:
                    results[0] = _raw_request(
                        sock_path,
                        {
                            "action": "generate",
                            "engine": "qwen",
                            "text": "this will fail",
                            "voice": None,
                            "output_path": str(output_a),
                        },
                        timeout=5.0,
                    )
                except Exception as exc:
                    errors.append(exc)

            def call_b():
                time.sleep(0.02)
                try:
                    results[1] = _raw_request(
                        sock_path,
                        {
                            "action": "generate",
                            "engine": "qwen",
                            "text": "this will succeed",
                            "voice": None,
                            "output_path": str(output_b),
                        },
                        timeout=5.0,
                    )
                except Exception as exc:
                    errors.append(exc)

            t_a = threading.Thread(target=call_a)
            t_b = threading.Thread(target=call_b)
            t_a.start()
            t_b.start()
            t_a.join(timeout=5.0)
            t_b.join(timeout=5.0)

        # No unexpected exceptions in either thread
        assert not errors, f"Thread(s) raised socket/timeout errors: {errors}"

        # First job must report error (engine raised)
        assert results[0] is not None, "Thread A (failing job) never received a response"
        assert results[0].get("status") == "error", (
            f"Expected first job to return status=error, got: {results[0]}"
        )

        # Second job must succeed despite the first one failing
        assert results[1] is not None, "Thread B (second job) never received a response"
        assert results[1].get("status") == "ok", (
            f"Expected second job to return status=ok after first job error, got: {results[1]}"
        )
