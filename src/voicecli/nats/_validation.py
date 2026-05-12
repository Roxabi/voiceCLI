"""Envelope/payload validation for TTS and STT NATS request handlers.

Each ``validate_*`` function inspects a decoded JSON payload dict and returns
a typed outcome the adapter maps to a reply error code — keeping all
request-shape logic out of the adapter's async ``handle()`` method.

Cross-reference
---------------
``src/voicecli/nats/_validate.py`` validates NATS identifier tokens (subject
tokens, queue-group names) via a single regex — a narrower, reusable concern.
This module is for per-handler envelope shape: required fields, types,
allowed values, and light cleaning (newline normalisation in TTS text).

Purity contract
---------------
This module is pure — no I/O, no async, no executor. Every branch is
reachable by a simple ``dict`` call, making it fully unit-testable without
any running NATS connection or loaded model.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

from roxabi_nats._validate import validate_nats_token

log = logging.getLogger(__name__)

# request_id pattern: 1–128 alphanumeric/underscore/hyphen chars.
# Mirrors the same check in both staging adapters (tts_adapter.py and
# stt_adapter.py).
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


# ---------------------------------------------------------------------------
# Outcome types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TtsValidationOutcome:
    """Result of ``validate_tts_request``.

    Attributes:
        error_code: Non-None when validation failed; the adapter maps this to
            the reply ``error`` field.  ``None`` means success.
        cleaned_text: Newline-normalised text ready for synthesis.  Present
            only on success (``error_code is None``).
        engine: Resolved engine name (payload value or ``default_engine``).
            Present only on success.
    """

    error_code: str | None
    cleaned_text: str | None = None
    engine: str | None = None


@dataclass(frozen=True)
class SttValidationOutcome:
    """Result of ``validate_stt_request``.

    Attributes:
        error_code: Non-None when validation failed.  ``None`` means success.
        overrides: Dict of optional transcription parameters supplied in the
            payload (None values excluded).  Present only on success; always
            a dict (possibly empty) — never ``None`` on success.
    """

    error_code: str | None
    overrides: dict | None = None


# ---------------------------------------------------------------------------
# TTS validation
# ---------------------------------------------------------------------------

_MALFORMED = TtsValidationOutcome(error_code="malformed_request")


def validate_tts_request(
    payload: dict,
    *,
    default_engine: str,
    engine_available: Callable[[str], bool],
) -> TtsValidationOutcome:
    """Validate a decoded TTS NATS request payload.

    Checks (in order):
    1. ``request_id`` present and non-empty.
    2. ``request_id`` matches ``^[A-Za-z0-9_-]{1,128}$``.
    3. ``text`` present and a ``str`` instance.
    4. Newline normalisation: ``\\r\\n`` → space, ``\\r`` → space, ``\\n`` → space.
    5. ``text`` non-empty after ``.strip()``.
    6. Engine token valid (``validate_nats_token``).
    7. Engine available per ``engine_available`` callback.

    Args:
        payload: Decoded JSON dict from the NATS message.
        default_engine: Engine name to use when payload omits ``"engine"``.
        engine_available: Callable that returns ``True`` iff the named engine
            is loaded and ready.

    Returns:
        ``TtsValidationOutcome`` with ``error_code=None`` on success, or a
        non-None ``error_code`` string on the first failing check.
    """
    # 1. request_id presence
    request_id = payload.get("request_id", "")
    if not request_id:
        return _MALFORMED

    # 2. request_id format
    if not re.match(r"^[A-Za-z0-9_-]{1,128}$", request_id):
        return _MALFORMED

    # 3. text presence and type
    text = payload.get("text")
    if not isinstance(text, str) or not text:
        return _MALFORMED

    # 4. Newline normalisation — \r\n first to avoid double-space
    _newline_count = text.count("\n") + text.count("\r")
    if _newline_count:
        text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        log.debug(
            "text_newlines_stripped",
            extra={
                "request_id": payload.get("request_id", ""),
                "removed": _newline_count,
            },
        )

    # 5. Non-empty after strip
    if not text.strip():
        log.warning(
            "text_empty_after_strip",
            extra={
                "request_id": payload.get("request_id", ""),
                "original_length": len(payload.get("text") or ""),
            },
        )
        return _MALFORMED

    # 6. Engine token validation
    engine = payload.get("engine") or default_engine
    try:
        validate_nats_token(engine, kind="engine")
    except ValueError:
        return _MALFORMED

    # 7. Engine availability
    if not engine_available(engine):
        return TtsValidationOutcome(error_code="engine_unavailable")

    return TtsValidationOutcome(error_code=None, cleaned_text=text, engine=engine)


# ---------------------------------------------------------------------------
# STT validation
# ---------------------------------------------------------------------------

_STT_MALFORMED = SttValidationOutcome(error_code="malformed_request")

# Optional fields: (key, accepted types) — mirrors stt_adapter.py handle()
_STT_OPTIONAL_TYPE_MAP: tuple[tuple[str, tuple[type, ...]], ...] = (
    ("language", (str,)),
    ("language_detection_threshold", (int, float)),
    ("language_detection_segments", (int,)),
    ("language_fallback", (str,)),
    ("initial_prompt", (str,)),
    ("task", (str,)),
)

_STT_OVERRIDE_KEYS = (
    "language",
    "language_detection_threshold",
    "language_detection_segments",
    "language_fallback",
    "initial_prompt",
    "task",
)


def validate_stt_request(payload: dict) -> SttValidationOutcome:
    """Validate a decoded STT NATS request payload.

    Checks (in order):
    1. ``request_id`` present and non-empty.
    2. ``request_id`` matches ``^[A-Za-z0-9_-]{1,128}$``.
    3. ``audio_b64`` present and a ``str`` instance.
    4. Optional field type checks (language, thresholds, task…).
       ``language_detection_segments`` must be ``int`` but NOT ``bool``
       (bool is a subclass of int in Python).
    5. ``task`` value in ``("transcribe", "translate")`` when present.
    6. Builds ``overrides`` dict from non-None optional fields.

    Args:
        payload: Decoded JSON dict from the NATS message.

    Returns:
        ``SttValidationOutcome`` with ``error_code=None`` and an ``overrides``
        dict (possibly empty) on success, or ``error_code="malformed_request"``
        on the first failing check.
    """
    # 1. request_id presence
    request_id = payload.get("request_id", "")
    if not request_id:
        return _STT_MALFORMED

    # 2. request_id format
    if not re.match(r"^[A-Za-z0-9_-]{1,128}$", request_id):
        return _STT_MALFORMED

    # 3. audio_b64 presence and type
    audio_b64 = payload.get("audio_b64")
    if not isinstance(audio_b64, str) or not audio_b64:
        return _STT_MALFORMED

    # 4. Optional field type checks
    for key, expected_types in _STT_OPTIONAL_TYPE_MAP:
        val = payload.get(key)
        if val is None:
            continue
        # bool is a subclass of int — reject explicitly for segments field
        if key == "language_detection_segments" and isinstance(val, bool):
            return _STT_MALFORMED
        if not isinstance(val, expected_types):
            return _STT_MALFORMED

    # 5. task value check
    task_val = payload.get("task")
    if task_val is not None and task_val not in ("transcribe", "translate"):
        return _STT_MALFORMED

    # 6. Build overrides dict (None values excluded)
    overrides = {k: v for k in _STT_OVERRIDE_KEYS if (v := payload.get(k)) is not None}

    return SttValidationOutcome(error_code=None, overrides=overrides)
