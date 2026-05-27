"""Tests for the [nats] table fallback (url + nkey_seed_path).

Covers:
  * load_nats_config — reads url and nkey_seed_path from voicecli.toml,
    expands ~ in nkey_seed_path, leaves keys absent when not set.
  * apply_nats_env_from_config — env vars take precedence over the toml
    values; absent env vars get populated from the toml.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voicecli.core.config import apply_nats_env_from_config, load_nats_config


def _write_toml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "voicecli.toml"
    p.write_text(body)
    return p


class TestLoadNatsConfig:
    def test_url_and_seed_path_loaded(self, tmp_path: Path) -> None:
        cfg = _write_toml(
            tmp_path,
            '[nats]\nurl = "nats://hub:4222"\nnkey_seed_path = "/abs/seed"\n',
        )
        result = load_nats_config(cfg)
        assert result["url"] == "nats://hub:4222"
        assert result["nkey_seed_path"] == "/abs/seed"

    def test_seed_path_tilde_expansion(self, tmp_path: Path) -> None:
        cfg = _write_toml(tmp_path, '[nats]\nnkey_seed_path = "~/.voicecli/nkeys/seed"\n')
        result = load_nats_config(cfg)
        assert result["nkey_seed_path"] == str(Path.home() / ".voicecli/nkeys/seed")

    def test_keys_absent_when_unset(self, tmp_path: Path) -> None:
        cfg = _write_toml(tmp_path, "[nats]\n")
        result = load_nats_config(cfg)
        assert "url" not in result
        assert "nkey_seed_path" not in result
        # default still applied for the existing key
        assert result["max_cached_engines"] == 2

    def test_no_config_file_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Force _find_config to find nothing so the test isn't perturbed by a
        # real voicecli.toml in the dev's home directory.
        monkeypatch.setattr("voicecli.core.config._find_config", lambda: None)
        result = load_nats_config(None)
        assert result["max_cached_engines"] == 2
        assert "url" not in result
        assert "nkey_seed_path" not in result


class TestApplyNatsEnvFromConfig:
    def test_toml_populates_unset_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NATS_URL", raising=False)
        monkeypatch.delenv("NATS_NKEY_SEED_PATH", raising=False)
        cfg = _write_toml(
            tmp_path,
            '[nats]\nurl = "nats://hub:4222"\nnkey_seed_path = "/abs/seed"\n',
        )
        apply_nats_env_from_config(cfg)
        import os

        assert os.environ.get("NATS_URL") == "nats://hub:4222"
        assert os.environ.get("NATS_NKEY_SEED_PATH") == "/abs/seed"

    def test_env_takes_priority_over_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NATS_URL", "nats://override:4222")
        monkeypatch.setenv("NATS_NKEY_SEED_PATH", "/env/seed")
        cfg = _write_toml(
            tmp_path,
            '[nats]\nurl = "nats://hub:4222"\nnkey_seed_path = "/toml/seed"\n',
        )
        apply_nats_env_from_config(cfg)
        import os

        assert os.environ["NATS_URL"] == "nats://override:4222"
        assert os.environ["NATS_NKEY_SEED_PATH"] == "/env/seed"

    def test_no_toml_keys_no_env_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NATS_URL", raising=False)
        cfg = _write_toml(tmp_path, "[nats]\nmax_cached_engines = 3\n")
        apply_nats_env_from_config(cfg)
        import os

        assert "NATS_URL" not in os.environ

    def test_tilde_expanded_when_setting_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NATS_NKEY_SEED_PATH", raising=False)
        cfg = _write_toml(tmp_path, '[nats]\nnkey_seed_path = "~/.voicecli/nkeys/seed"\n')
        apply_nats_env_from_config(cfg)
        import os

        assert os.environ["NATS_NKEY_SEED_PATH"] == str(Path.home() / ".voicecli/nkeys/seed")
