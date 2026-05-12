"""Unit tests for voicecli.nats._validation (issue #147)."""

from __future__ import annotations

import pytest

from voicecli.nats._validation import (
    validate_stt_request,
    validate_tts_request,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AVAILABLE = {"mock", "qwen-fast"}
_engine_available = lambda e: e in _AVAILABLE  # noqa: E731


def _valid_tts_payload(**overrides: object) -> dict:
    base: dict = {
        "request_id": "req-001",
        "text": "Hello world",
    }
    base.update(overrides)
    return base


def _valid_stt_payload(**overrides: object) -> dict:
    base: dict = {
        "request_id": "req-001",
        "audio_b64": "AAAA",
    }
    base.update(overrides)
    return base


# ===========================================================================
# TestValidateTtsRequest
# ===========================================================================


class TestValidateTtsRequest:
    # -----------------------------------------------------------------------
    # request_id validation
    # -----------------------------------------------------------------------

    def test_missing_request_id_returns_malformed(self) -> None:
        # Arrange
        payload = {"text": "Hello"}
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.error_code == "malformed_request"
        assert result.cleaned_text is None
        assert result.engine is None

    def test_empty_request_id_returns_malformed(self) -> None:
        # Arrange
        payload = _valid_tts_payload(request_id="")
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.error_code == "malformed_request"

    def test_request_id_with_invalid_chars_returns_malformed(self) -> None:
        # Arrange — spaces and wildcards are forbidden
        payload = _valid_tts_payload(request_id="bad req id!")
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.error_code == "malformed_request"

    def test_request_id_too_long_returns_malformed(self) -> None:
        # Arrange — 129 chars exceeds the 128-char cap
        payload = _valid_tts_payload(request_id="a" * 129)
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.error_code == "malformed_request"

    def test_request_id_exactly_128_chars_is_valid(self) -> None:
        # Arrange
        payload = _valid_tts_payload(request_id="a" * 128)
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert — valid 128-char request_id produces a success outcome.
        assert result.error_code is None
        assert result.engine is not None

    # -----------------------------------------------------------------------
    # text validation
    # -----------------------------------------------------------------------

    def test_missing_text_returns_malformed(self) -> None:
        # Arrange
        payload: dict = {"request_id": "req-001"}
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.error_code == "malformed_request"

    def test_text_not_a_string_returns_malformed(self) -> None:
        # Arrange
        payload = _valid_tts_payload(text=42)
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.error_code == "malformed_request"

    def test_text_none_returns_malformed(self) -> None:
        # Arrange
        payload = _valid_tts_payload(text=None)
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.error_code == "malformed_request"

    # -----------------------------------------------------------------------
    # newline stripping in text
    # -----------------------------------------------------------------------

    def test_text_with_lf_is_stripped_to_spaces(self) -> None:
        # Arrange
        payload = _valid_tts_payload(text="Hello\nworld")
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.error_code is None
        assert result.cleaned_text is not None
        assert "\n" not in result.cleaned_text
        assert "\r" not in result.cleaned_text

    def test_text_with_cr_is_stripped_to_spaces(self) -> None:
        # Arrange
        payload = _valid_tts_payload(text="Hello\rworld")
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.error_code is None
        assert "\r" not in (result.cleaned_text or "")

    def test_text_with_crlf_is_stripped_to_spaces(self) -> None:
        # Arrange
        payload = _valid_tts_payload(text="Hello\r\nworld")
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.error_code is None
        assert "\r" not in (result.cleaned_text or "")
        assert "\n" not in (result.cleaned_text or "")

    def test_text_empty_after_stripping_returns_malformed(self) -> None:
        # Arrange — only newlines → stripped to spaces → .strip() → empty
        payload = _valid_tts_payload(text="\n\n\r\n")
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.error_code == "malformed_request"

    # -----------------------------------------------------------------------
    # engine resolution and validation
    # -----------------------------------------------------------------------

    def test_missing_engine_defaults_to_default_engine(self) -> None:
        # Arrange — no engine key in payload
        payload: dict = {"request_id": "req-001", "text": "Hello world"}
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.error_code is None
        assert result.engine == "mock"

    def test_engine_none_defaults_to_default_engine(self) -> None:
        # Arrange
        payload = _valid_tts_payload(engine=None)
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.error_code is None
        assert result.engine == "mock"

    def test_engine_with_space_returns_malformed(self) -> None:
        # Arrange — space is not a valid NATS token character
        payload = _valid_tts_payload(engine="bad engine")
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.error_code == "malformed_request"

    def test_engine_with_wildcard_returns_malformed(self) -> None:
        # Arrange — NATS wildcard chars are rejected
        payload = _valid_tts_payload(engine="qwen*")
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.error_code == "malformed_request"

    def test_engine_not_available_returns_engine_unavailable(self) -> None:
        # Arrange — syntactically valid engine name not in the available set
        payload = _valid_tts_payload(engine="voxtral")
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.error_code == "engine_unavailable"

    def test_valid_engine_passes(self) -> None:
        # Arrange
        payload = _valid_tts_payload(engine="qwen-fast")
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.error_code is None
        assert result.engine == "qwen-fast"

    # -----------------------------------------------------------------------
    # happy path
    # -----------------------------------------------------------------------

    def test_valid_payload_returns_success(self) -> None:
        # Arrange
        payload = _valid_tts_payload()
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.error_code is None
        assert result.cleaned_text == "Hello world"
        assert result.engine == "mock"

    def test_valid_payload_cleaned_text_equals_text_when_no_newlines(self) -> None:
        # Arrange
        payload = _valid_tts_payload(text="Bonjour le monde")
        # Act
        result = validate_tts_request(
            payload, default_engine="mock", engine_available=_engine_available
        )
        # Assert
        assert result.cleaned_text == "Bonjour le monde"

    def test_outcome_is_frozen_dataclass(self) -> None:
        # Arrange + Act
        result = validate_tts_request(
            _valid_tts_payload(), default_engine="mock", engine_available=_engine_available
        )
        # Assert — frozen dataclass raises FrozenInstanceError on mutation attempt
        with pytest.raises(Exception):  # noqa: PT011
            result.error_code = "mutated"  # type: ignore[misc]


# ===========================================================================
# TestValidateSttRequest
# ===========================================================================


class TestValidateSttRequest:
    # -----------------------------------------------------------------------
    # request_id validation
    # -----------------------------------------------------------------------

    def test_missing_request_id_returns_malformed(self) -> None:
        # Arrange
        payload = {"audio_b64": "AAAA"}
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code == "malformed_request"
        assert result.overrides is None

    def test_empty_request_id_returns_malformed(self) -> None:
        # Arrange
        payload = _valid_stt_payload(request_id="")
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code == "malformed_request"

    def test_request_id_with_invalid_chars_returns_malformed(self) -> None:
        # Arrange
        payload = _valid_stt_payload(request_id="bad id!")
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code == "malformed_request"

    def test_request_id_too_long_returns_malformed(self) -> None:
        # Arrange — 129 chars
        payload = _valid_stt_payload(request_id="b" * 129)
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code == "malformed_request"

    def test_request_id_exactly_128_chars_is_valid(self) -> None:
        # Arrange
        payload = _valid_stt_payload(request_id="b" * 128)
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code is None

    # -----------------------------------------------------------------------
    # audio_b64 validation
    # -----------------------------------------------------------------------

    def test_missing_audio_b64_returns_malformed(self) -> None:
        # Arrange
        payload: dict = {"request_id": "req-001"}
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code == "malformed_request"

    def test_audio_b64_not_a_string_returns_malformed(self) -> None:
        # Arrange
        payload = _valid_stt_payload(audio_b64=123)
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code == "malformed_request"

    def test_audio_b64_none_returns_malformed(self) -> None:
        # Arrange
        payload = _valid_stt_payload(audio_b64=None)
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code == "malformed_request"

    # -----------------------------------------------------------------------
    # optional field type validation (parametrized)
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("language", 42),
            ("language", ["en"]),
            ("language_detection_threshold", "high"),
            ("language_detection_threshold", ["0.5"]),
            ("language_detection_segments", "3"),
            ("language_detection_segments", [3]),
            ("language_fallback", 0),
            ("language_fallback", True),
            ("initial_prompt", 99),
            ("initial_prompt", {"text": "hi"}),
            ("task", 1),
            ("task", ["transcribe"]),
        ],
    )
    def test_optional_field_wrong_type_returns_malformed(
        self, field: str, bad_value: object
    ) -> None:
        # Arrange
        payload = _valid_stt_payload(**{field: bad_value})
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code == "malformed_request", (
            f"Expected malformed_request for {field}={bad_value!r}"
        )

    def test_language_detection_segments_bool_true_rejected(self) -> None:
        # Arrange — bool is a subclass of int but must be explicitly rejected
        payload = _valid_stt_payload(language_detection_segments=True)
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code == "malformed_request"

    def test_language_detection_segments_bool_false_rejected(self) -> None:
        # Arrange
        payload = _valid_stt_payload(language_detection_segments=False)
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code == "malformed_request"

    def test_language_detection_segments_int_accepted(self) -> None:
        # Arrange
        payload = _valid_stt_payload(language_detection_segments=3)
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code is None

    def test_language_detection_threshold_int_accepted(self) -> None:
        # Arrange — int is a valid type for this field
        payload = _valid_stt_payload(language_detection_threshold=1)
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code is None

    def test_language_detection_threshold_float_accepted(self) -> None:
        # Arrange
        payload = _valid_stt_payload(language_detection_threshold=0.75)
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code is None

    # -----------------------------------------------------------------------
    # task field validation
    # -----------------------------------------------------------------------

    def test_task_invalid_value_returns_malformed(self) -> None:
        # Arrange — valid str type but unknown task
        payload = _valid_stt_payload(task="summarise")
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code == "malformed_request"

    def test_task_transcribe_is_valid(self) -> None:
        # Arrange
        payload = _valid_stt_payload(task="transcribe")
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code is None

    def test_task_translate_is_valid(self) -> None:
        # Arrange
        payload = _valid_stt_payload(task="translate")
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code is None

    # -----------------------------------------------------------------------
    # happy path — overrides dict
    # -----------------------------------------------------------------------

    def test_valid_minimal_payload_returns_success(self) -> None:
        # Arrange
        payload = _valid_stt_payload()
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code is None
        assert result.overrides == {}

    def test_valid_payload_with_optional_fields_included_in_overrides(self) -> None:
        # Arrange
        payload = _valid_stt_payload(language="fr", task="transcribe")
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code is None
        assert result.overrides is not None
        assert result.overrides.get("language") == "fr"
        assert result.overrides.get("task") == "transcribe"

    def test_overrides_contains_only_provided_optional_fields(self) -> None:
        # Arrange — only language provided; other optional fields absent
        payload = _valid_stt_payload(language="en")
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.overrides is not None
        assert set(result.overrides.keys()) == {"language"}

    def test_overrides_excludes_none_optional_fields(self) -> None:
        # Arrange — explicitly provide None for an optional field
        payload = _valid_stt_payload(language=None)
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.overrides is not None
        assert "language" not in result.overrides

    def test_all_optional_fields_present_in_overrides(self) -> None:
        # Arrange
        payload = _valid_stt_payload(
            language="es",
            language_detection_threshold=0.5,
            language_detection_segments=5,
            language_fallback="en",
            initial_prompt="Transcribe carefully.",
            task="translate",
        )
        # Act
        result = validate_stt_request(payload)
        # Assert
        assert result.error_code is None
        assert result.overrides is not None
        assert result.overrides["language"] == "es"
        assert result.overrides["language_detection_threshold"] == 0.5
        assert result.overrides["language_detection_segments"] == 5
        assert result.overrides["language_fallback"] == "en"
        assert result.overrides["initial_prompt"] == "Transcribe carefully."
        assert result.overrides["task"] == "translate"

    def test_outcome_is_frozen_dataclass(self) -> None:
        # Arrange + Act
        result = validate_stt_request(_valid_stt_payload())
        # Assert — frozen dataclass raises FrozenInstanceError on mutation attempt
        with pytest.raises(Exception):  # noqa: PT011
            result.error_code = "mutated"  # type: ignore[misc]
