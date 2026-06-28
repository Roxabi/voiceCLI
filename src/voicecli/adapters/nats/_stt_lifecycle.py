"""STT lifecycle list/status handlers."""

from __future__ import annotations

from voicecli.runtime.transcribe import MODELS


def build_stt_list_data(*, default_model: str) -> dict:
    return {
        "models": [{"name": m} for m in MODELS],
        "default_model": default_model,
    }


def build_stt_status_data(adapter) -> dict:
    return {
        "model_loaded": adapter.model_loaded,
        "model_warm": adapter._model_warm,
        "active_requests": adapter.max_concurrent - adapter._sem._value,
        "default_model": adapter.default_model,
    }
