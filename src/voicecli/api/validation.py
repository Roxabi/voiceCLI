"""API input validation — ParamValidationError + boundary checks."""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from voicecli.core.utils import _Unrestricted

_MAX_STRING_LEN = 256
_MAX_TEXT_LEN = 100_000


class ParamValidationError(ValueError):
    """Raised when a public-API parameter fails validation.

    Subclass of ValueError so existing `except ValueError` callers keep
    working; NATS adapters catch this narrower type to avoid
    misclassifying path-escape or engine ValueErrors as param faults.
    """


def _check_str(name: str, value, *, max_len: int = _MAX_STRING_LEN) -> None:
    """Validate a string parameter: type, length, no embedded newlines."""
    if value is None:
        return
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string, got {type(value).__name__}")
    if len(value) > max_len:
        raise ParamValidationError(f"{name} exceeds maximum length ({max_len} chars)")
    if "\n" in value or "\r" in value:
        raise ParamValidationError(f"{name} must not contain newline characters")


def _check_float(name: str, value, lo: float, hi: float) -> None:
    """Validate a float parameter: type, finite, within range."""
    if value is None:
        return
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number, got bool")
    if not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    if math.isnan(value) or math.isinf(value):
        raise ParamValidationError(f"{name} must be finite, got {value}")
    if not (lo <= value <= hi):
        raise ParamValidationError(f"{name} must be between {lo} and {hi}, got {value}")


def _check_int(name: str, value, lo: int, hi: int) -> None:
    """Validate an integer parameter: type, within range."""
    if value is None:
        return
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer, got bool")
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, got {type(value).__name__}")
    if not (lo <= value <= hi):
        raise ParamValidationError(f"{name} must be between {lo} and {hi}, got {value}")


def _validate_tts_params(
    *,
    text: str | Path | None = None,
    engine: str | None = None,
    voice: str | None = None,
    language: str | None = None,
    segment_gap: int | None = None,
    crossfade: int | None = None,
    chunk_size: int | None = None,
    extra_kwargs: dict | None = None,
) -> None:
    """Validate TTS parameters at the API boundary.

    Raises TypeError or ValueError for invalid input.
    """
    # Text length (only for raw strings, not file paths)
    if isinstance(text, str) and Path(text).suffix not in (".md", ".txt"):
        _check_str("text", text, max_len=_MAX_TEXT_LEN)

    _check_str("engine", engine, max_len=64)
    _check_str("voice", voice)
    _check_str("language", language)
    _check_int("segment_gap", segment_gap, 0, 30_000)
    _check_int("crossfade", crossfade, 0, 10_000)
    _check_int("chunk_size", chunk_size, 1, 10_000)

    if extra_kwargs:
        for field in ("instruct", "accent", "personality", "speed", "emotion"):
            _check_str(field, extra_kwargs.get(field))
        _check_float("exaggeration", extra_kwargs.get("exaggeration"), 0.0, 2.0)
        _check_float("cfg_weight", extra_kwargs.get("cfg_weight"), 0.0, 1.0)


def _validate_output_path(
    output_path: Path,
    *,
    allowed_base: Path | _Unrestricted,
) -> Path:
    """Validate output path stays within allowed_base; create parent dirs.

    Args:
        output_path: Target output path.
        allowed_base: Base directory the path must stay within, or
            ``UNRESTRICTED`` when the caller owns the trust boundary (CLI
            ``--output`` override, server-controlled scratch path). Pass
            ``UNRESTRICTED`` explicitly — there is no implicit default, so
            each caller declares its trust model.

    Returns:
        Resolved absolute path. For ``UNRESTRICTED`` no parent dirs are
        created (caller is responsible).

    Raises:
        ValueError: If ``output_path`` escapes ``allowed_base``.
    """
    resolved = output_path.expanduser().resolve()

    if isinstance(allowed_base, _Unrestricted):
        return resolved

    base = allowed_base.expanduser().resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        raise ValueError(
            f"Output path is outside the allowed directory {allowed_base}. "
            "Pass an explicit --output to override."
        )
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved
