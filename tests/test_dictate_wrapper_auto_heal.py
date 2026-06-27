"""Smoke tests for scripts/wrappers/voicecli-dictate.sh auto-heal hooks."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "scripts" / "wrappers" / "voicecli-dictate.sh"


def test_wrapper_shell_syntax_valid() -> None:
    subprocess.run(["bash", "-n", str(WRAPPER)], check=True)


def test_wrapper_documents_auto_heal_env() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    assert "VOICECLI_AUTO_HEAL" in text
    assert "_heal_voicecli" in text
    assert "VOICECLI_TRACK_BRANCH" in text


def test_run_dictate_uses_dynamic_voicecli_bin() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    assert "_run_dictate()" in text
    assert 'local run_cmd=( "$VOICECLI_BIN" dictate nats )' in text


def test_heal_refuses_dirty_repo() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    assert "git -C" in text and "status --porcelain" in text
