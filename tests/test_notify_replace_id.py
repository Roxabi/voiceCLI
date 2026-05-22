"""Regression test for the notify-send replace-ID.

The dictate flow notifies from three distinct Python processes (foreground
``dictate nats``, background ``nats_recorder`` subprocess, and the second
``dictate nats`` press that transcribes). Python's ``hash()`` is salted per
process via PYTHONHASHSEED, so a hash-derived replace-ID would produce three
different IDs and stack three bubbles instead of replacing in place. Lock
the constant down.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_replace_id_is_stable_across_processes() -> None:
    script = textwrap.dedent(
        """
        from voicecli.ui.stt_client import _NOTIFY_REPLACE_ID
        print(_NOTIFY_REPLACE_ID)
        """
    )
    a = subprocess.run(
        [sys.executable, "-c", script], check=True, capture_output=True, text=True
    ).stdout.strip()
    b = subprocess.run(
        [sys.executable, "-c", script], check=True, capture_output=True, text=True
    ).stdout.strip()
    assert a == b, (
        f"_NOTIFY_REPLACE_ID must be deterministic across processes "
        f"(got {a!r} and {b!r}) — otherwise the dictate notification stacks."
    )


def test_replace_id_is_a_positive_integer_string() -> None:
    from voicecli.ui.stt_client import _NOTIFY_REPLACE_ID

    # notify-send -r requires an integer.
    n = int(_NOTIFY_REPLACE_ID)
    assert n > 0
    assert n < 2**31  # safely fits any libnotify version
