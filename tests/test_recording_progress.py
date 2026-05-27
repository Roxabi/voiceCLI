"""Tests for the progress-notification loop in nats_mic_recorder.

The recorder subprocess refreshes the desktop notification every ~1.5s with
elapsed seconds so the user sees the recording is alive. Verifies that the
loop emits the expected text format and exits promptly when the stop event
is set.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

from voicecli.ui import nats_mic_recorder


def test_progress_loop_emits_elapsed_seconds(monkeypatch) -> None:
    # Tick fast so the test stays well under 1s.
    monkeypatch.setattr(nats_mic_recorder, "PROGRESS_TICK_SECONDS", 0.05)
    stop_event = threading.Event()
    started = time.monotonic()

    captured: list[str] = []

    def fake_notify(body: str, timeout: int = 3000) -> None:
        captured.append(body)

    with patch("voicecli.ui.dictate_client.notify", side_effect=fake_notify):
        t = threading.Thread(
            target=nats_mic_recorder._progress_notify_loop,
            args=(stop_event, started),
            daemon=True,
        )
        t.start()
        # Let at least 2 ticks happen.
        time.sleep(0.18)
        stop_event.set()
        t.join(timeout=1.0)

    assert not t.is_alive(), "thread should exit shortly after stop_event.set()"
    assert len(captured) >= 2, f"expected ≥2 ticks, got {captured!r}"
    for body in captured:
        assert body.startswith("Recording... ") and body.endswith("s")


def test_progress_loop_exits_immediately_when_already_stopped(monkeypatch) -> None:
    """If stop_event is set before the loop starts, no notifications fire."""
    monkeypatch.setattr(nats_mic_recorder, "PROGRESS_TICK_SECONDS", 0.05)
    stop_event = threading.Event()
    stop_event.set()
    started = time.monotonic()

    captured: list[str] = []

    def fake_notify(body: str, timeout: int = 3000) -> None:
        captured.append(body)

    with patch("voicecli.ui.dictate_client.notify", side_effect=fake_notify):
        t = threading.Thread(
            target=nats_mic_recorder._progress_notify_loop,
            args=(stop_event, started),
            daemon=True,
        )
        t.start()
        t.join(timeout=1.0)

    assert not t.is_alive()
    assert captured == []
