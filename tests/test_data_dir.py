"""Tests for voicecli.config — get_data_dir() and _find_config() resolution."""

from pathlib import Path

import pytest

from voicecli.config import get_data_dir, _find_config


class TestGetDataDir:
    """get_data_dir() priority and validation tests."""

    def test_returns_env_var_when_set(self, monkeypatch, tmp_path):
        env_dir = tmp_path / "env_override"
        env_dir.mkdir()
        monkeypatch.setenv("VOICECLI_DATA_DIR", str(env_dir))
        assert get_data_dir() == env_dir

    def test_returns_new_dir_when_neither_exists(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("VOICECLI_DATA_DIR", raising=False)
        expected = tmp_path / ".roxabi" / "voicecli"
        assert get_data_dir() == expected

    def test_returns_legacy_when_old_exists_and_new_absent(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("VOICECLI_DATA_DIR", raising=False)
        old_dir = tmp_path / ".voicecli"
        old_dir.mkdir(parents=True)
        assert get_data_dir() == old_dir

    def test_prefers_new_dir_when_both_exist(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("VOICECLI_DATA_DIR", raising=False)
        new_dir = tmp_path / ".roxabi" / "voicecli"
        new_dir.mkdir(parents=True)
        old_dir = tmp_path / ".voicecli"
        old_dir.mkdir(parents=True)
        assert get_data_dir() == new_dir

    def test_rejects_relative_env_path(self, monkeypatch):
        monkeypatch.setenv("VOICECLI_DATA_DIR", "relative/path")
        with pytest.raises(ValueError, match="must be an absolute path"):
            get_data_dir()

    def test_rejects_env_path_that_is_a_file(self, monkeypatch, tmp_path):
        file_path = tmp_path / "not_a_dir"
        file_path.write_text("data")
        monkeypatch.setenv("VOICECLI_DATA_DIR", str(file_path))
        with pytest.raises(ValueError, match="must be a directory"):
            get_data_dir()

    def test_does_not_create_directory(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("VOICECLI_DATA_DIR", raising=False)
        result = get_data_dir()
        assert not result.exists()

    def test_resolves_tilde_in_env_var(self, monkeypatch, tmp_path):
        env_dir = tmp_path / "env_override"
        env_dir.mkdir()
        monkeypatch.setenv("VOICECLI_DATA_DIR", "~/env_override")
        monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path) if p == "~" else p)
        assert get_data_dir() == env_dir


class TestFindConfig:
    """_find_config() path resolution tests."""

    def test_finds_config_in_canonical_location(self, monkeypatch, tmp_path):
        canonical = tmp_path / ".roxabi" / "voicecli"
        canonical.mkdir(parents=True)
        config = canonical / "voicecli.toml"
        config.write_text("[defaults]\nlanguage = 'French'\n")
        monkeypatch.setattr("voicecli.config.VOICECLI_DIR", canonical, raising=False)
        assert _find_config() == config

    def test_falls_back_to_legacy_location(self, monkeypatch, tmp_path):
        legacy = tmp_path / ".voicecli"
        legacy.mkdir(parents=True)
        config = legacy / "voicecli.toml"
        config.write_text("[defaults]\nlanguage = 'French'\n")
        monkeypatch.setattr("voicecli.config.VOICECLI_DIR", legacy, raising=False)
        assert _find_config() == config

    def test_returns_none_when_no_config_found(self, monkeypatch, tmp_path):
        monkeypatch.setattr("voicecli.config.VOICECLI_DIR", tmp_path / "nonexistent", raising=False)
        monkeypatch.chdir(tmp_path)
        assert _find_config() is None

    def test_walks_up_from_subdirectory(self, monkeypatch, tmp_path):
        subdir = tmp_path / "sub" / "dir"
        subdir.mkdir(parents=True)
        config = tmp_path / "voicecli.toml"
        config.write_text("[defaults]\nlanguage = 'French'\n")
        monkeypatch.setattr("voicecli.config.VOICECLI_DIR", tmp_path / "nonexistent", raising=False)
        monkeypatch.chdir(subdir)
        assert _find_config() == config
