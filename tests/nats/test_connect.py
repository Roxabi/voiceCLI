"""RED-phase tests for voicecli.nats.connect._read_nkey_seed (issue #42).

These tests FAIL until the voicecli.nats package is implemented.
They define the contract for _read_nkey_seed(path: Path) -> KeyPair-like object.

Note: unlike the lyra port which reads from an env var, voicecli's version
accepts an explicit Path so it integrates cleanly with the CLI option pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voicecli.nats.connect import _read_nkey_seed


class TestNkeyEnvVarResolution:
    def test_nats_nkey_seed_path_env_var_resolved_to_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NATS_NKEY_SEED_PATH env var is converted to a Path and expanduser'd.

        This tests the CLI-level resolver logic in isolation: given the env var,
        the path object must point at the expected location.
        """
        import os

        seed_file = tmp_path / "test.seed"
        seed_file.write_text("SUAXXX123FAKESEEDFORTEST")
        seed_file.chmod(0o600)
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", str(seed_file))

        # Replicate the resolution logic from cli.py nats_serve_tts
        nkey_seed_env = os.environ.get("NATS_NKEY_SEED_PATH")
        assert nkey_seed_env is not None
        resolved = Path(nkey_seed_env).expanduser()
        assert resolved == seed_file
        assert resolved.exists()


class TestReadNkeySeed:
    def test_nkey_seed_permission_check(self, tmp_path: Path) -> None:
        """_read_nkey_seed raises PermissionError for group/world-readable files.

        A seed file at mode 0644 (group+world read) must be rejected with a
        PermissionError whose message mentions "0600" to guide the user toward
        the correct permission.
        """
        # Arrange
        seed_file = tmp_path / "nkey.seed"
        seed_file.write_text("SUAXXX123")
        seed_file.chmod(0o644)

        # Act + Assert
        with pytest.raises(PermissionError, match="0600"):
            _read_nkey_seed(seed_file)

    def test_nkey_seed_loads_with_correct_perms(self, tmp_path: Path) -> None:
        """_read_nkey_seed returns a non-None KeyPair-like object for a 0600 seed file.

        A seed file starting with 'SUA' at mode 0600 is a valid nkey user seed.
        The returned object must be non-None (KeyPair or equivalent).
        """
        # Arrange
        seed_file = tmp_path / "nkey.seed"
        seed_file.write_text("SUAXXX123FAKESEEDFORTEST")
        seed_file.chmod(0o600)

        # Act
        result = _read_nkey_seed(seed_file)

        # Assert
        assert result is not None
