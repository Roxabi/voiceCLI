"""Tests for TTS parameter input validation (#33)."""

import math
from unittest.mock import patch

import pytest

from voicecli.api import _validate_tts_params


# ── String field validation ──────────────────────────────────────────────────


class TestStringValidation:
    def test_accepts_valid_strings(self):
        _validate_tts_params(engine="qwen", voice="Ryan", language="French")

    def test_accepts_none_strings(self):
        _validate_tts_params(engine=None, voice=None, language=None)

    def test_rejects_newline_in_engine(self):
        with pytest.raises(ValueError, match="newline"):
            _validate_tts_params(engine="qwen\nevil")

    def test_rejects_carriage_return_in_voice(self):
        with pytest.raises(ValueError, match="newline"):
            _validate_tts_params(voice="Ryan\revil")

    def test_rejects_overlong_engine(self):
        with pytest.raises(ValueError, match="maximum length"):
            _validate_tts_params(engine="x" * 65)

    def test_rejects_overlong_voice(self):
        with pytest.raises(ValueError, match="maximum length"):
            _validate_tts_params(voice="x" * 257)

    def test_rejects_overlong_language(self):
        with pytest.raises(ValueError, match="maximum length"):
            _validate_tts_params(language="x" * 257)

    def test_rejects_non_string_engine(self):
        with pytest.raises(TypeError, match="must be a string"):
            _validate_tts_params(engine=123)

    def test_rejects_overlong_text(self):
        with pytest.raises(ValueError, match="maximum length"):
            _validate_tts_params(text="x" * 100_001)

    def test_accepts_long_text_within_limit(self):
        _validate_tts_params(text="x" * 100_000)

    def test_rejects_newline_in_text(self):
        """Raw text strings must not contain newlines (protocol safety)."""
        with pytest.raises(ValueError, match="newline"):
            _validate_tts_params(text="line1\nline2")

    def test_skips_text_validation_for_md_path(self):
        """File paths (.md) skip text string validation."""
        _validate_tts_params(text="script.md")

    def test_skips_text_validation_for_txt_path(self):
        """File paths (.txt) skip text string validation."""
        _validate_tts_params(text="article.txt")


# ── Extra kwargs string fields ───────────────────────────────────────────────


class TestExtraKwargsStrings:
    @pytest.mark.parametrize("field", ["instruct", "accent", "personality", "speed", "emotion"])
    def test_rejects_overlong_extra_string(self, field):
        with pytest.raises(ValueError, match="maximum length"):
            _validate_tts_params(extra_kwargs={field: "x" * 257})

    @pytest.mark.parametrize("field", ["instruct", "accent", "personality", "speed", "emotion"])
    def test_rejects_newline_in_extra_string(self, field):
        with pytest.raises(ValueError, match="newline"):
            _validate_tts_params(extra_kwargs={field: "valid\ninjection"})

    @pytest.mark.parametrize("field", ["instruct", "accent", "personality", "speed", "emotion"])
    def test_accepts_valid_extra_string(self, field):
        _validate_tts_params(extra_kwargs={field: "Léger accent provençal"})


# ── Float field validation ───────────────────────────────────────────────────


class TestFloatValidation:
    def test_accepts_valid_exaggeration(self):
        _validate_tts_params(extra_kwargs={"exaggeration": 0.7})

    def test_accepts_exaggeration_boundaries(self):
        _validate_tts_params(extra_kwargs={"exaggeration": 0.0})
        _validate_tts_params(extra_kwargs={"exaggeration": 2.0})

    def test_rejects_negative_exaggeration(self):
        with pytest.raises(ValueError, match="between 0.0 and 2.0"):
            _validate_tts_params(extra_kwargs={"exaggeration": -0.1})

    def test_rejects_too_large_exaggeration(self):
        with pytest.raises(ValueError, match="between 0.0 and 2.0"):
            _validate_tts_params(extra_kwargs={"exaggeration": 2.1})

    def test_rejects_nan_exaggeration(self):
        with pytest.raises(ValueError, match="must be finite"):
            _validate_tts_params(extra_kwargs={"exaggeration": float("nan")})

    def test_rejects_inf_exaggeration(self):
        with pytest.raises(ValueError, match="must be finite"):
            _validate_tts_params(extra_kwargs={"exaggeration": float("inf")})

    def test_accepts_valid_cfg_weight(self):
        _validate_tts_params(extra_kwargs={"cfg_weight": 0.5})

    def test_accepts_cfg_weight_boundaries(self):
        _validate_tts_params(extra_kwargs={"cfg_weight": 0.0})
        _validate_tts_params(extra_kwargs={"cfg_weight": 1.0})

    def test_rejects_negative_cfg_weight(self):
        with pytest.raises(ValueError, match="between 0.0 and 1.0"):
            _validate_tts_params(extra_kwargs={"cfg_weight": -0.1})

    def test_rejects_too_large_cfg_weight(self):
        with pytest.raises(ValueError, match="between 0.0 and 1.0"):
            _validate_tts_params(extra_kwargs={"cfg_weight": 1.1})

    def test_rejects_non_numeric_exaggeration(self):
        with pytest.raises(TypeError, match="must be a number"):
            _validate_tts_params(extra_kwargs={"exaggeration": "high"})

    def test_accepts_int_as_float(self):
        """Int values should be accepted for float fields."""
        _validate_tts_params(extra_kwargs={"exaggeration": 1, "cfg_weight": 0})

    def test_rejects_bool_exaggeration(self):
        with pytest.raises(TypeError, match="got bool"):
            _validate_tts_params(extra_kwargs={"exaggeration": True})

    def test_rejects_bool_cfg_weight(self):
        with pytest.raises(TypeError, match="got bool"):
            _validate_tts_params(extra_kwargs={"cfg_weight": False})


# ── Integer field validation ─────────────────────────────────────────────────


class TestIntValidation:
    def test_accepts_valid_segment_gap(self):
        _validate_tts_params(segment_gap=200)

    def test_accepts_zero_segment_gap(self):
        _validate_tts_params(segment_gap=0)

    def test_rejects_negative_segment_gap(self):
        with pytest.raises(ValueError, match="between 0 and 30000"):
            _validate_tts_params(segment_gap=-1)

    def test_rejects_too_large_segment_gap(self):
        with pytest.raises(ValueError, match="between 0 and 30000"):
            _validate_tts_params(segment_gap=30_001)

    def test_accepts_valid_crossfade(self):
        _validate_tts_params(crossfade=50)

    def test_rejects_negative_crossfade(self):
        with pytest.raises(ValueError, match="between 0 and 10000"):
            _validate_tts_params(crossfade=-1)

    def test_accepts_valid_chunk_size(self):
        _validate_tts_params(chunk_size=500)

    def test_rejects_zero_chunk_size(self):
        with pytest.raises(ValueError, match="between 1 and 10000"):
            _validate_tts_params(chunk_size=0)

    def test_rejects_non_int_segment_gap(self):
        with pytest.raises(TypeError, match="must be an integer"):
            _validate_tts_params(segment_gap=200.5)

    def test_rejects_bool_segment_gap(self):
        with pytest.raises(TypeError, match="got bool"):
            _validate_tts_params(segment_gap=True)

    def test_rejects_bool_crossfade(self):
        with pytest.raises(TypeError, match="got bool"):
            _validate_tts_params(crossfade=False)


# ── Integration: validation in generate/clone ────────────────────────────────


class TestGenerateValidation:
    def test_generate_rejects_nan_exaggeration(self):
        from voicecli.api import generate

        with (
            patch("voicecli.core.config.load_defaults", return_value={}),
            pytest.raises(ValueError, match="must be finite"),
        ):
            generate("Hello", exaggeration=float("nan"))

    def test_generate_rejects_overlong_instruct(self):
        from voicecli.api import generate

        with (
            patch("voicecli.core.config.load_defaults", return_value={}),
            pytest.raises(ValueError, match="maximum length"),
        ):
            generate("Hello", instruct="x" * 257)


class TestCloneValidation:
    def test_clone_rejects_negative_exaggeration(self):
        from voicecli.api import clone

        with (
            patch("voicecli.core.config.load_defaults", return_value={}),
            pytest.raises(ValueError, match="between 0.0 and 2.0"),
        ):
            clone("Hello", ref="/tmp/fake.wav", exaggeration=-1.0)


# ── Daemon validation ────────────────────────────────────────────────────────


class TestDaemonSanitizeRequest:
    def test_strips_newlines_from_engine(self):
        from voicecli.runtime.daemon import _sanitize_request

        req = {"engine": "qwen\nevil", "text": "hello"}
        err = _sanitize_request(req)
        assert err is None
        assert req["engine"] == "qwenevil"

    def test_strips_newlines_from_text(self):
        from voicecli.runtime.daemon import _sanitize_request

        req = {"engine": "qwen", "text": "line1\nline2"}
        err = _sanitize_request(req)
        assert err is None
        assert req["text"] == "line1 line2"

    def test_rejects_overlong_engine(self):
        from voicecli.runtime.daemon import _sanitize_request

        req = {"engine": "x" * 65, "text": "hello"}
        err = _sanitize_request(req)
        assert err is not None
        assert "maximum length" in err

    def test_rejects_overlong_text(self):
        from voicecli.runtime.daemon import _sanitize_request

        req = {"engine": "qwen", "text": "x" * 100_001}
        err = _sanitize_request(req)
        assert err is not None
        assert "maximum length" in err

    def test_rejects_nan_exaggeration(self):
        from voicecli.runtime.daemon import _sanitize_request

        req = {"engine": "qwen", "text": "hello", "exaggeration": float("nan")}
        err = _sanitize_request(req)
        assert err is not None
        assert "finite" in err

    def test_rejects_out_of_range_cfg_weight(self):
        from voicecli.runtime.daemon import _sanitize_request

        req = {"engine": "qwen", "text": "hello", "cfg_weight": 5.0}
        err = _sanitize_request(req)
        assert err is not None
        assert "between" in err

    def test_accepts_valid_request(self):
        from voicecli.runtime.daemon import _sanitize_request

        req = {
            "engine": "qwen",
            "text": "Hello world",
            "exaggeration": 0.7,
            "cfg_weight": 0.3,
        }
        err = _sanitize_request(req)
        assert err is None

    def test_clamps_string_exaggeration(self):
        """Non-numeric exaggeration from JSON should be rejected."""
        from voicecli.runtime.daemon import _sanitize_request

        req = {"engine": "qwen", "text": "hello", "exaggeration": "high"}
        err = _sanitize_request(req)
        assert err is not None
        assert "must be a number" in err

    def test_rejects_negative_segment_gap(self):
        from voicecli.runtime.daemon import _sanitize_request

        req = {"engine": "qwen", "text": "hello", "segment_gap": -100}
        err = _sanitize_request(req)
        assert err is not None
        assert "between" in err

    def test_rejects_too_large_crossfade(self):
        from voicecli.runtime.daemon import _sanitize_request

        req = {"engine": "qwen", "text": "hello", "crossfade": 99999}
        err = _sanitize_request(req)
        assert err is not None
        assert "between" in err

    def test_accepts_valid_segment_gap_and_crossfade(self):
        from voicecli.runtime.daemon import _sanitize_request

        req = {"engine": "qwen", "text": "hello", "segment_gap": 200, "crossfade": 50}
        err = _sanitize_request(req)
        assert err is None

    def test_sanitizes_segment_strings(self):
        from voicecli.runtime.daemon import _sanitize_request

        req = {
            "engine": "qwen",
            "text": "hello",
            "segments": [{"text": "seg1\ntext", "instruct": "loud\nevil", "language": "French"}],
        }
        err = _sanitize_request(req)
        assert err is None
        assert req["segments"][0]["text"] == "seg1 text"
        assert req["segments"][0]["instruct"] == "loudevil"

    def test_rejects_overlong_segment_instruct(self):
        from voicecli.runtime.daemon import _sanitize_request

        req = {
            "engine": "qwen",
            "text": "hello",
            "segments": [{"text": "ok", "instruct": "x" * 257}],
        }
        err = _sanitize_request(req)
        assert err is not None
        assert "segments[0].instruct" in err

    def test_rejects_segment_nan_exaggeration(self):
        from voicecli.runtime.daemon import _sanitize_request

        req = {
            "engine": "qwen",
            "text": "hello",
            "segments": [{"text": "ok", "exaggeration": float("nan")}],
        }
        err = _sanitize_request(req)
        assert err is not None
        assert "segments[0].exaggeration" in err

    def test_rejects_segment_out_of_range_crossfade(self):
        from voicecli.runtime.daemon import _sanitize_request

        req = {
            "engine": "qwen",
            "text": "hello",
            "segments": [{"text": "ok", "crossfade": -1}],
        }
        err = _sanitize_request(req)
        assert err is not None
        assert "segments[0].crossfade" in err
