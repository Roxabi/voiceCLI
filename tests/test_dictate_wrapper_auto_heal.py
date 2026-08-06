"""Tests for scripts/wrappers/voicecli-dictate.sh path-repair + upgrade heal."""

from __future__ import annotations

import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "scripts" / "wrappers" / "voicecli-dictate.sh"


def test_wrapper_shell_syntax_valid() -> None:
    subprocess.run(["bash", "-n", str(WRAPPER)], check=True)


def test_wrapper_documents_split_heal() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    assert "VOICECLI_AUTO_HEAL" in text
    assert "_relink_voicecli" in text
    assert "_upgrade_voicecli" in text
    assert "_heal_voicecli" in text  # back-compat alias
    assert "_voicecli_missing" in text
    assert "VOICECLI_TRACK_BRANCH" in text
    assert "projects/roxabi/voiceCLI" in text
    # Path-repair must not gate on dirty tree (upgrade still does).
    assert "status --porcelain --untracked-files=no" in text
    # Function body for relink must not invoke git (comments may mention it).
    relink_body = text.split("_relink_voicecli()")[1].split("_upgrade_voicecli()")[0]
    assert "status --porcelain" not in relink_body
    assert "git fetch" not in relink_body
    assert "git checkout" not in relink_body
    assert "git pull" not in relink_body


def test_run_dictate_uses_dynamic_voicecli_bin() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    assert "_run_dictate()" in text
    assert 'local run_cmd=( "$VOICECLI_BIN" dictate nats )' in text


def test_upgrade_refuses_dirty_repo() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    assert "git -C" in text and "status --porcelain --untracked-files=no" in text


def test_cli_needs_upgrade_is_narrow() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    assert "_cli_needs_upgrade" in text
    assert "_cli_needs_relink" in text
    # Upgrade must not match bare ENOENT (fleet / false-heal risk).
    upgrade_fn = text.split("_cli_needs_upgrade()")[1].split("_cli_needs_")[0]
    if "No such file or directory" in upgrade_fn.split("grep")[1] if "grep" in upgrade_fn else "":
        pytest.fail("upgrade heal must not match bare 'No such file or directory'")
    assert "No such command 'nats" in text
    assert "ImportError" in text


def test_install_shortcut_pins_repo() -> None:
    install = (REPO_ROOT / "scripts" / "install-shortcut.sh").read_text(encoding="utf-8")
    assert "repo-path" in install
    assert "Pinned VOICECLI_REPO" in install


def _write_fake_voicecli(path: Path, marker: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/bash
            set -euo pipefail
            # Empty nats-host skips TCP probe in the wrapper.
            if [ "${{1:-}}" = "dictate" ] && [ "${{2:-}}" = "nats-host" ]; then
                exit 0
            fi
            if [ "${{1:-}}" = "dictate" ] && [ "${{2:-}}" = "nats" ]; then
                printf 'dictate-ok\\n' >"{marker}"
                exit 0
            fi
            echo "unexpected: $*" >&2
            exit 99
            """
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_relink_repairs_dangling_symlink_offline(tmp_path: Path) -> None:
    """Priced recovery invariant: dangling ~/.local/bin/voicecli → relink → dictate runs.

    No network, no git fetch: pure path-repair.
    """
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    bin_dir = home / ".local" / "bin"
    state_dir = home / ".local" / "state" / "voicecli"
    bin_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)
    (repo / ".git").mkdir(parents=True)  # plain checkout marker
    (repo / ".venv" / "bin").mkdir(parents=True)

    marker = tmp_path / "dictate-ran"
    fake_bin = repo / ".venv" / "bin" / "voicecli"
    _write_fake_voicecli(fake_bin, marker)

    # Dangling symlink at the usual install location.
    dangling = bin_dir / "voicecli"
    dangling.symlink_to("/nonexistent/voicecli-old-path")

    # Dirty worktree would block *upgrade*; relink must ignore it.
    # Simulate dirt by leaving a tracked-looking file; no real git needed for relink.
    (repo / "WIP.md").write_text("dirty", encoding="utf-8")

    heal_log = state_dir / "heal.log"
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"/usr/bin:/bin:{bin_dir}",
            "VOICECLI_REPO": str(repo),
            "VOICECLI_AUTO_HEAL": "1",
            "VOICECLI_HEAL_LOG": str(heal_log),
            # Ensure no accidental real notify / git network deps matter.
            "TMPDIR": str(tmp_path / "tmp"),
        }
    )
    (tmp_path / "tmp").mkdir(exist_ok=True)

    # Stub notify-send / nc out of PATH so wrapper stays offline-local.
    # (wrapper prefers /usr/bin first — that's fine if they exist; nc probe is
    # skipped when nats-host prints nothing.)

    result = subprocess.run(
        ["bash", str(WRAPPER)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, (
        f"wrapper exit {result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}\n"
        f"heal_log={heal_log.read_text() if heal_log.exists() else ''!r}"
    )
    assert marker.exists(), "fake voicecli dictate nats was not invoked after relink"
    assert marker.read_text(encoding="utf-8").strip() == "dictate-ok"

    # Symlink retargeted to the new checkout binary.
    assert dangling.is_symlink()
    assert dangling.resolve() == fake_bin.resolve()

    # Heal log records relink success (not upgrade/git).
    log_text = heal_log.read_text(encoding="utf-8") if heal_log.exists() else ""
    assert "relink ok" in log_text
    assert "upgrade start" not in log_text
    assert "git fetch" not in log_text


def test_relink_disabled_when_auto_heal_off(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (repo / ".git").mkdir(parents=True)
    (repo / ".venv" / "bin").mkdir(parents=True)
    marker = tmp_path / "dictate-ran"
    fake_bin = repo / ".venv" / "bin" / "voicecli"
    _write_fake_voicecli(fake_bin, marker)

    dangling = bin_dir / "voicecli"
    dangling.symlink_to("/nonexistent/voicecli-old-path")

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"/usr/bin:/bin:{bin_dir}",
            "VOICECLI_REPO": str(repo),
            "VOICECLI_AUTO_HEAL": "0",
            "TMPDIR": str(tmp_path / "tmp"),
        }
    )
    (tmp_path / "tmp").mkdir(exist_ok=True)

    result = subprocess.run(
        ["bash", str(WRAPPER)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode != 0
    assert not marker.exists()
    # Still dangling.
    assert dangling.is_symlink()
    assert not dangling.exists()
