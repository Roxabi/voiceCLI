"""Tests for voicecli.nats.config resolvers (issues #49, #61).

Covers:
  * _resolve_engine — CLI arg wins; VOICECLI_ENGINE > LYRA_TTS_ENGINE > toml > DEFAULT_ENGINE;
    load_config exception is swallowed.
  * _resolve_model — CLI arg wins; VOICECLI_MODEL > toml [stt].model > DEFAULT_MODEL;
    load_config exception is swallowed.
"""

from __future__ import annotations

import pytest

try:
    from voicecli.nats.config import (
        DEFAULT_ENGINE,
        DEFAULT_MODEL,
        _resolve_engine,
        _resolve_model,
    )

    _IMPORT_ERROR: ImportError | None = None
except ImportError as _e:
    _IMPORT_ERROR = _e
    _resolve_engine = None  # type: ignore[assignment]
    _resolve_model = None  # type: ignore[assignment]
    DEFAULT_ENGINE = None  # type: ignore[assignment]
    DEFAULT_MODEL = None  # type: ignore[assignment]


def _require_imports() -> None:
    if _IMPORT_ERROR is not None:
        pytest.fail(f"voicecli.nats.config not yet implemented (RED): {_IMPORT_ERROR}")


class TestResolveEngine:
    def test_cli_arg_wins_over_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _require_imports()
        # Arrange — env vars are set but CLI arg takes precedence
        monkeypatch.setenv("VOICECLI_ENGINE", "env-engine")
        monkeypatch.setenv("LYRA_TTS_ENGINE", "lyra-engine")

        # Act
        result = _resolve_engine("cli-override")

        # Assert
        assert result == "cli-override"

    def test_voicecli_engine_wins_over_lyra_and_toml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _require_imports()
        # Arrange
        monkeypatch.setenv("VOICECLI_ENGINE", "voicecli-engine")
        monkeypatch.delenv("LYRA_TTS_ENGINE", raising=False)

        # Act
        result = _resolve_engine(None)

        # Assert
        assert result == "voicecli-engine"

    def test_voicecli_engine_wins_when_both_env_vars_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VOICECLI_ENGINE takes precedence over LYRA_TTS_ENGINE when both are set.

        The precedence doc promises VOICECLI_ENGINE > LYRA_TTS_ENGINE, but the sibling
        test only proves VOICECLI wins when LYRA is absent. This test pins the "both
        set" case so a future swap of the env-var lookup order is caught.
        """
        _require_imports()
        # Arrange — both env vars set with different distinguishable values
        monkeypatch.setenv("VOICECLI_ENGINE", "voicecli-wins")
        monkeypatch.setenv("LYRA_TTS_ENGINE", "lyra-loses")

        # Act
        result = _resolve_engine(None)

        # Assert
        assert result == "voicecli-wins"

    def test_lyra_tts_engine_fallback_when_voicecli_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _require_imports()
        # Arrange — only LYRA_TTS_ENGINE is set
        monkeypatch.delenv("VOICECLI_ENGINE", raising=False)
        monkeypatch.setenv("LYRA_TTS_ENGINE", "lyra-engine")

        # Act
        result = _resolve_engine(None)

        # Assert
        assert result == "lyra-engine"

    def test_toml_engine_fallback_when_no_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _require_imports()
        # Arrange — no env vars; load_tts_config returns toml engine
        monkeypatch.delenv("VOICECLI_ENGINE", raising=False)
        monkeypatch.delenv("LYRA_TTS_ENGINE", raising=False)
        monkeypatch.setattr(
            "voicecli.config.load_tts_config",
            lambda: {"default_engine": "toml-engine"},
        )

        # Act
        result = _resolve_engine(None)

        # Assert
        assert result == "toml-engine"

    def test_default_engine_when_all_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _require_imports()
        # Arrange — no env vars; toml has no engine entry
        monkeypatch.delenv("VOICECLI_ENGINE", raising=False)
        monkeypatch.delenv("LYRA_TTS_ENGINE", raising=False)
        monkeypatch.setattr(
            "voicecli.config.load_tts_config",
            lambda: {"default_engine": None},
        )

        # Act
        result = _resolve_engine(None)

        # Assert
        assert result == DEFAULT_ENGINE

    def test_load_config_exception_falls_through_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _require_imports()
        # Arrange — no env vars; load_tts_config raises
        monkeypatch.delenv("VOICECLI_ENGINE", raising=False)
        monkeypatch.delenv("LYRA_TTS_ENGINE", raising=False)

        def _raise():
            raise RuntimeError("config file not found")

        monkeypatch.setattr("voicecli.config.load_tts_config", _raise)

        # Act — must not raise; exception is swallowed
        result = _resolve_engine(None)

        # Assert
        assert result == DEFAULT_ENGINE


class TestResolveModel:
    def test_cli_arg_wins_over_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _require_imports()
        # Arrange — env var set, CLI arg should win
        monkeypatch.setenv("VOICECLI_MODEL", "small")

        # Act
        result = _resolve_model("tiny")

        # Assert
        assert result == "tiny"

    def test_env_var_used_when_cli_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _require_imports()
        # Arrange
        monkeypatch.setenv("VOICECLI_MODEL", "small")

        # Act
        result = _resolve_model(None)

        # Assert
        assert result == "small"

    def test_toml_model_fallback_when_no_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _require_imports()
        # Arrange — no env var; load_config returns stt.model
        monkeypatch.delenv("VOICECLI_MODEL", raising=False)
        monkeypatch.setattr(
            "voicecli.config.load_config",
            lambda: {"stt": {"model": "toml-model"}},
        )

        # Act
        result = _resolve_model(None)

        # Assert
        assert result == "toml-model"

    def test_default_model_when_all_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _require_imports()
        # Arrange — no env var; toml has no stt.model entry
        monkeypatch.delenv("VOICECLI_MODEL", raising=False)
        monkeypatch.setattr(
            "voicecli.config.load_config",
            lambda: {},
        )

        # Act
        result = _resolve_model(None)

        # Assert
        assert result == DEFAULT_MODEL

    def test_load_config_exception_falls_through_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _require_imports()
        # Arrange — no env var; load_config raises
        monkeypatch.delenv("VOICECLI_MODEL", raising=False)

        def _raise():
            raise RuntimeError("config file not found")

        monkeypatch.setattr("voicecli.config.load_config", _raise)

        # Act — must not raise; exception is swallowed
        result = _resolve_model(None)

        # Assert
        assert result == DEFAULT_MODEL
