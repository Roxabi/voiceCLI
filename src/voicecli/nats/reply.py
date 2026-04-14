"""ADR-044 reply envelope helpers."""

from __future__ import annotations

import json

CONTRACT_VERSION = "1"


def build_reply(*, ok: bool, request_id: str, **fields) -> dict:
    """ADR-044 reply envelope. Stamps contract_version + request_id; merges caller fields last."""
    return {"contract_version": CONTRACT_VERSION, "request_id": request_id, "ok": ok, **fields}


def encode_reply(reply: dict) -> bytes:
    return json.dumps(reply, separators=(",", ":")).encode("utf-8")
