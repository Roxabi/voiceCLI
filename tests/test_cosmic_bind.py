"""Tests for scripts/_cosmic_bind.py — idempotent COSMIC binding writer."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "scripts" / "_cosmic_bind.py"


def run(custom: Path, mods: str, key: str, wrapper: str, *legacy: str) -> None:
    subprocess.run(
        [
            sys.executable,
            str(HELPER),
            str(custom),
            mods,
            key,
            wrapper,
            "voiceCLI dictate",
            *legacy,
        ],
        check=True,
    )


def test_creates_file_when_missing(tmp_path: Path) -> None:
    custom = tmp_path / "custom"
    run(custom, "Ctrl", "space", "/usr/local/bin/voicecli-dictate")
    out = custom.read_text()
    assert out.startswith("{\n")
    assert out.endswith("}\n")
    assert 'key: "space"' in out
    assert 'Spawn("/usr/local/bin/voicecli-dictate")' in out
    assert "Ctrl,\n" in out


def test_preserves_unrelated_existing_entries(tmp_path: Path) -> None:
    custom = tmp_path / "custom"
    custom.write_text(
        "{\n"
        "    (\n"
        "        modifiers: [\n"
        "            Super,\n"
        "            Shift,\n"
        "        ],\n"
        '        key: "s",\n'
        '        description: Some("screenshot"),\n'
        '    ): Spawn("cosmic-screenshot"),\n'
        "}\n"
    )
    run(custom, "Ctrl", "space", "/home/me/.local/bin/voicecli-dictate")
    out = custom.read_text()
    # Original screenshot entry preserved.
    assert 'Spawn("cosmic-screenshot")' in out
    # New entry added.
    assert 'Spawn("/home/me/.local/bin/voicecli-dictate")' in out


def test_drops_same_key_modifiers_conflict(tmp_path: Path) -> None:
    custom = tmp_path / "custom"
    custom.write_text(
        "{\n"
        "    (\n"
        "        modifiers: [\n"
        "            Ctrl,\n"
        "        ],\n"
        '        key: "space",\n'
        '        description: Some("old binding"),\n'
        '    ): Spawn("/old/path"),\n'
        "}\n"
    )
    run(custom, "Ctrl", "space", "/new/path")
    out = custom.read_text()
    assert "/old/path" not in out
    assert 'Spawn("/new/path")' in out
    # Exactly one (… ): Spawn(…) entry remains.
    assert out.count("): Spawn(") == 1


def test_drops_legacy_basename(tmp_path: Path) -> None:
    """Legacy `voicecli-dictate-nats` entry on a *different* key still gets cleaned up."""
    custom = tmp_path / "custom"
    custom.write_text(
        "{\n"
        "    (\n"
        "        modifiers: [\n"
        "            Alt,\n"
        "        ],\n"
        '        key: "space",\n'
        '        description: Some("old voiceCLI"),\n'
        '    ): Spawn("/home/me/.local/bin/voicecli-dictate-nats"),\n'
        "}\n"
    )
    run(custom, "Ctrl", "space", "/home/me/.local/bin/voicecli-dictate", "voicecli-dictate-nats")
    out = custom.read_text()
    assert "voicecli-dictate-nats" not in out
    assert 'Spawn("/home/me/.local/bin/voicecli-dictate")' in out


def test_idempotent_when_already_present(tmp_path: Path) -> None:
    custom = tmp_path / "custom"
    run(custom, "Ctrl", "space", "/wrap")
    first = custom.read_text()
    run(custom, "Ctrl", "space", "/wrap")
    second = custom.read_text()
    assert first == second
    assert second.count("): Spawn(") == 1


def test_drops_same_wrapper_target_with_different_key(tmp_path: Path) -> None:
    """Re-running with a new keybinding for the same wrapper rebinds — no orphan."""
    custom = tmp_path / "custom"
    run(custom, "Ctrl", "space", "/wrap")
    run(custom, "Alt", "F9", "/wrap")
    out = custom.read_text()
    assert out.count("): Spawn(") == 1
    assert 'key: "F9"' in out
