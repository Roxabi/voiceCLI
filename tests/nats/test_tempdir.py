"""RED-phase tests for voicecli.adapters.nats.tempdir (issue #42).

These tests FAIL until voicecli.adapters.nats.tempdir is implemented.
They define the contract for scoped_path() and cleanup():
- scoped_path produces unique, deterministic paths under /tmp/voicecli-nats/
- cleanup removes files silently, including missing paths
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voicecli.adapters.nats.tempdir import cleanup, scoped_path


class TestScopedPath:
    def test_scoped_path_unique_per_request_id(self) -> None:
        """Different request IDs produce different paths; filename encodes the ID."""
        # Arrange / Act
        path_1 = scoped_path("req-1", "wav")
        path_2 = scoped_path("req-2", "wav")

        # Assert
        assert path_1 != path_2
        assert path_1.name == "req-1.wav"

    def test_scoped_path_under_voicecli_nats(self) -> None:
        """scoped_path returns a path inside a voicecli-nats temp directory."""
        # Arrange / Act
        path = scoped_path("req-1", "wav")

        # Assert
        assert "/voicecli-nats/" in str(path)


class TestScopedPathSecurity:
    """Every case here raises with the same canonical ``escapes temp root``
    message. The null-byte variant is guarded explicitly in ``scoped_path``
    so the rejection source is consistent across all traversal shapes — no
    reliance on CPython's OS-layer messages. ``monkeypatch`` redirects
    ``TEMP_ROOT`` to an isolated ``tmp_path`` so the rejected calls do not
    race with live adapter processes in ``/tmp/voicecli-nats/``.
    """

    def test_path_traversal_raises_value_error(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        import voicecli.adapters.nats.tempdir as tempdir_mod

        monkeypatch.setattr(tempdir_mod, "TEMP_ROOT", tmp_path / "voicecli-nats")
        with pytest.raises(ValueError, match="escapes temp root"):
            scoped_path("../../etc/passwd", "wav")

    def test_dotdot_request_id_raises_value_error(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        import voicecli.adapters.nats.tempdir as tempdir_mod

        monkeypatch.setattr(tempdir_mod, "TEMP_ROOT", tmp_path / "voicecli-nats")
        with pytest.raises(ValueError, match="escapes temp root"):
            scoped_path("../foo", "wav")

    def test_absolute_path_request_id_rejected(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """request_id that is an absolute path must not escape TEMP_ROOT.

        Belt-and-suspenders — the adapter allowlist rejects `/` at ingestion,
        but scoped_path is the last line of defense and should reject absolute
        paths directly (joinpath with an absolute component replaces the base).
        """
        import voicecli.adapters.nats.tempdir as tempdir_mod

        monkeypatch.setattr(tempdir_mod, "TEMP_ROOT", tmp_path / "voicecli-nats")
        with pytest.raises(ValueError, match="escapes temp root"):
            scoped_path("/etc/passwd", "wav")

    def test_null_byte_request_id_rejected(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """request_id with embedded null byte must raise ValueError.

        The explicit null-byte guard in ``scoped_path`` surfaces the canonical
        ``escapes temp root`` message rather than CPython's OS-layer
        ``embedded null character``. Pinning the message means a future
        refactor that silently strips the null byte would fail loudly.
        """
        import voicecli.adapters.nats.tempdir as tempdir_mod

        monkeypatch.setattr(tempdir_mod, "TEMP_ROOT", tmp_path / "voicecli-nats")
        with pytest.raises(ValueError, match="escapes temp root.*null byte"):
            scoped_path("req\x00evil", "wav")


class TestCleanup:
    def test_cleanup_removes_file(self, tmp_path: Path) -> None:
        """cleanup deletes an existing file without raising."""
        # Arrange
        target = tmp_path / "output.wav"
        target.write_bytes(b"RIFF")

        # Act
        cleanup(target)

        # Assert
        assert not target.exists()

    def test_cleanup_does_not_raise_on_missing_file(self, tmp_path: Path) -> None:
        """cleanup is a no-op (no exception) when the path does not exist."""
        # Arrange
        missing = tmp_path / "does_not_exist.wav"

        # Act + Assert — must not raise
        cleanup(missing)


class TestTempRootMode:
    def test_temp_root_created_with_mode_0o700(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """TEMP_ROOT is created with mode 0o700 after first scoped_path call.

        Uses tmp_path so the real /tmp/voicecli-nats is never touched — avoids racing
        with live adapter processes or pytest-xdist workers.
        """
        import os
        import stat

        import voicecli.adapters.nats.tempdir as tempdir_mod

        # Arrange — redirect TEMP_ROOT to an isolated tmp_path subdir that does NOT
        # yet exist, so scoped_path's mkdir actually runs
        sandbox = tmp_path / "voicecli-nats"
        monkeypatch.setattr(tempdir_mod, "TEMP_ROOT", sandbox)

        # Act
        scoped_path("req-v1", "wav")

        # Assert
        assert stat.S_IMODE(os.stat(sandbox).st_mode) == 0o700

    def test_temp_root_chmod_enforces_mode_on_existing_dir(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """If TEMP_ROOT already exists with looser perms, scoped_path enforces 0o700.

        Guards the gap where mkdir(mode=..., exist_ok=True) is a no-op on the OS
        syscall for existing dirs — without the explicit chmod, a prior install or
        attacker-created dir could silently grant broader read access.
        """
        import os
        import stat

        import voicecli.adapters.nats.tempdir as tempdir_mod

        # Arrange — pre-create the sandbox with world-readable perms. The
        # explicit chmod after mkdir is required because other tests in this
        # suite (test_connect invokes nats-serve which sets umask 0o077) can
        # leave a tightened process umask that would mask mkdir's mode.
        sandbox = tmp_path / "voicecli-nats"
        sandbox.mkdir(mode=0o755)
        sandbox.chmod(0o755)
        monkeypatch.setattr(tempdir_mod, "TEMP_ROOT", sandbox)
        assert stat.S_IMODE(os.stat(sandbox).st_mode) == 0o755

        # Act
        scoped_path("req-v1", "wav")

        # Assert — mode must be corrected to 0o700
        assert stat.S_IMODE(os.stat(sandbox).st_mode) == 0o700
