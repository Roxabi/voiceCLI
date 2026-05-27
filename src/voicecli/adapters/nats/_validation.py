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

from voicecli.adapters.nats.requests import (
    MalformedRequestError,
    SttRequest,
    TtsRequest,
)

log = logging.getLogger(__name__)

# request_id pattern: 1–128 alphanumeric/underscore/hyphen chars.
# Mirrors the same check in both staging adapters (synthesize_adapter.py and
# transcribe_adapter.py).
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
    request: TtsRequest | None = None


@dataclass(frozen=True)
class SttValidationOutcome:
    """Result of ``validate_stt_request``.

    Attributes:
        error_code: Non-None when validation failed.  ``None`` means success.
        overrides: Dict of optional transcription parameters supplied in the
            payload (None values excluded).  Present only on success; always
            a dict (possibly empty) — never ``None`` on success.
        request: The successfully parsed ``SttRequest``, present only on success.
    """

    error_code: str | None
    overrides: dict | None = None
    request: SttRequest | None = None


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

    Delegates typed construction to ``TtsRequest.from_payload()``; performs
    semantic validation (newline stripping, engine token, availability) on the
    typed result.

    Args:
        payload: Decoded JSON dict from the NATS message.
        default_engine: Engine name to use when payload omits ``"engine"``.
        engine_available: Callable that returns ``True`` iff the named engine
            is loaded and ready.

    Returns:
        ``TtsValidationOutcome`` with ``error_code=None`` on success, or a
        non-None ``error_code`` string on the first failing check.
    """
    try:
        req = TtsRequest.from_payload(payload)
    except (MalformedRequestError, TypeError, KeyError):
        return _MALFORMED

    if not _REQUEST_ID_RE.match(req.request_id):
        return _MALFORMED

    # Newline normalisation — \r\n first to avoid double-space
    text = req.text
    _newline_count = text.count("\n") + text.count("\r")
    if _newline_count:
        text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        log.debug(
            "text_newlines_stripped",
            extra={
                "request_id": req.request_id,
                "removed": _newline_count,
            },
        )

    # Non-empty after strip
    if not text.strip():
        log.warning(
            "text_empty_after_strip",
            extra={
                "request_id": req.request_id,
                "original_length": len(req.text),
            },
        )
        return _MALFORMED

    # Engine token validation
    engine = req.engine or default_engine
    try:
        validate_nats_token(engine, kind="engine")
    except ValueError:
        return _MALFORMED

    # Engine availability
    if not engine_available(engine):
        return TtsValidationOutcome(error_code="engine_unavailable")

    return TtsValidationOutcome(error_code=None, cleaned_text=text, engine=engine, request=req)


# ---------------------------------------------------------------------------
# STT validation
# ---------------------------------------------------------------------------

_STT_MALFORMED = SttValidationOutcome(error_code="malformed_request")


def validate_stt_request(payload: dict) -> SttValidationOutcome:
    """Validate a decoded STT NATS request payload.

    Delegates typed construction to ``SttRequest.from_payload()``; returns
    ``malformed_request`` on any type mismatch or format violation.

    Args:
        payload: Decoded JSON dict from the NATS message.

    Returns:
        ``SttValidationOutcome`` with ``error_code=None`` and an ``overrides``
        dict (possibly empty) on success, or ``error_code="malformed_request"``
        on the first failing check.
    """
    try:
        req = SttRequest.from_payload(payload)
    except (MalformedRequestError, TypeError, KeyError):
        return _STT_MALFORMED

    if not _REQUEST_ID_RE.match(req.request_id):
        return _STT_MALFORMED

    return SttValidationOutcome(error_code=None, overrides=req.to_overrides(), request=req)
