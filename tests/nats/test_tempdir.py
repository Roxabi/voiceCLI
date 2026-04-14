"""RED-phase tests for voicecli.nats.tempdir (issue #42).

These tests FAIL until voicecli.nats.tempdir is implemented.
They define the contract for scoped_path() and cleanup():
- scoped_path produces unique, deterministic paths under /tmp/voicecli-nats/
- cleanup removes files silently, including missing paths
"""

from __future__ import annotations

from pathlib import Path

from voicecli.nats.tempdir import cleanup, scoped_path


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
    def test_path_traversal_raises_value_error(self) -> None:
        """scoped_path raises ValueError when request_id escapes TEMP_ROOT."""
        import pytest

        with pytest.raises(ValueError, match="escapes temp root"):
            scoped_path("../../etc/passwd", "wav")

    def test_dotdot_request_id_raises_value_error(self) -> None:
        """request_id with .. components must be rejected."""
        import pytest

        with pytest.raises(ValueError, match="escapes temp root"):
            scoped_path("../foo", "wav")


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
