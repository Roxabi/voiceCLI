"""Typed request dataclasses for NATS adapter ingress."""

from __future__ import annotations

from dataclasses import dataclass

from roxabi_blobs import BlobRef


class MalformedRequestError(ValueError):
    """Raised when a NATS payload fails typed construction or format checks."""


@dataclass(frozen=True)
class SttRequest:
    """Typed STT request constructed from NATS payload dict."""

    blob_ref: BlobRef
    request_id: str
    trace_id: str = ""
    contract_version: str = ""
    language: str | None = None
    language_detection_threshold: float | None = None
    language_detection_segments: int | None = None
    language_fallback: str | None = None
    initial_prompt: str | None = None
    task: str | None = None

    @classmethod
    def from_payload(cls, payload: dict) -> "SttRequest":
        """Construct from decoded JSON payload; raise MalformedRequestError on type mismatch."""
        raw_ref = payload.get("blob_ref")
        if not isinstance(raw_ref, dict):
            raise MalformedRequestError("blob_ref must be an object")
        try:
            blob_ref = BlobRef.model_validate(raw_ref)
        except (TypeError, ValueError) as e:
            raise MalformedRequestError(f"blob_ref invalid: {e}") from e

        request_id = payload.get("request_id", "")
        if not isinstance(request_id, str) or not request_id:
            raise MalformedRequestError("request_id must be a non-empty str")

        language = payload.get("language")
        if language is not None and not isinstance(language, str):
            raise MalformedRequestError("language must be a str")

        threshold = payload.get("language_detection_threshold")
        if threshold is not None and (
            not isinstance(threshold, (int, float)) or isinstance(threshold, bool)
        ):
            raise MalformedRequestError(
                "language_detection_threshold must be int or float (not bool)"
            )

        segments = payload.get("language_detection_segments")
        if segments is not None:
            if isinstance(segments, bool) or not isinstance(segments, int):
                raise MalformedRequestError("language_detection_segments must be an int (not bool)")

        fallback = payload.get("language_fallback")
        if fallback is not None and not isinstance(fallback, str):
            raise MalformedRequestError("language_fallback must be a str")

        prompt = payload.get("initial_prompt")
        if prompt is not None and not isinstance(prompt, str):
            raise MalformedRequestError("initial_prompt must be a str")

        task = payload.get("task")
        if task is not None and task not in ("transcribe", "translate"):
            raise MalformedRequestError("task must be 'transcribe' or 'translate'")

        trace_id = payload.get("trace_id")
        if trace_id is not None and not isinstance(trace_id, str):
            raise MalformedRequestError("trace_id must be a str")

        contract_version = payload.get("contract_version")
        if contract_version is not None and not isinstance(contract_version, str):
            raise MalformedRequestError("contract_version must be a str")

        return cls(
            blob_ref=blob_ref,
            request_id=request_id,
            trace_id=trace_id or "",
            contract_version=contract_version or "",
            language=language,
            language_detection_threshold=float(threshold) if threshold is not None else None,
            language_detection_segments=segments,
            language_fallback=fallback,
            initial_prompt=prompt,
            task=task,
        )

    def to_overrides(self) -> dict:
        """Build kwargs dict for api.transcribe from non-None optional fields."""
        overrides: dict = {}
        if self.language is not None:
            overrides["language"] = self.language
        if self.language_detection_threshold is not None:
            overrides["language_detection_threshold"] = self.language_detection_threshold
        if self.language_detection_segments is not None:
            overrides["language_detection_segments"] = self.language_detection_segments
        if self.language_fallback is not None:
            overrides["language_fallback"] = self.language_fallback
        if self.initial_prompt is not None:
            overrides["initial_prompt"] = self.initial_prompt
        if self.task is not None:
            overrides["task"] = self.task
        return overrides


@dataclass(frozen=True)
class TtsRequest:
    """Typed TTS request constructed from NATS payload dict."""

    text: str
    request_id: str
    trace_id: str = ""
    contract_version: str = ""
    engine: str | None = None
    language: str | None = None
    voice: str | None = None
    speed: float | None = None
    exaggeration: float | None = None
    cfg_weight: float | None = None
    accent: str | None = None
    personality: str | None = None
    emotion: str | None = None
    chunked: bool | None = None
    chunk_size: int | None = None
    segment_gap: float | None = None
    crossfade: float | None = None
    fallback_language: str | None = None

    @classmethod
    def from_payload(cls, payload: dict) -> "TtsRequest":
        """Construct from decoded JSON payload; raise MalformedRequestError on type mismatch."""
        text = payload.get("text")
        if not isinstance(text, str) or not text:
            raise MalformedRequestError("text must be a non-empty str")

        request_id = payload.get("request_id", "")
        if not isinstance(request_id, str) or not request_id:
            raise MalformedRequestError("request_id must be a non-empty str")

        engine = payload.get("engine", "")
        if engine is not None and not isinstance(engine, str):
            raise MalformedRequestError("engine must be a str or None")

        speed = payload.get("speed")
        if speed is not None and (not isinstance(speed, (int, float)) or isinstance(speed, bool)):
            raise MalformedRequestError("speed must be int or float (not bool)")

        exaggeration = payload.get("exaggeration")
        if exaggeration is not None and (
            not isinstance(exaggeration, (int, float)) or isinstance(exaggeration, bool)
        ):
            raise MalformedRequestError("exaggeration must be int or float (not bool)")

        cfg_weight = payload.get("cfg_weight")
        if cfg_weight is not None and (
            not isinstance(cfg_weight, (int, float)) or isinstance(cfg_weight, bool)
        ):
            raise MalformedRequestError("cfg_weight must be int or float (not bool)")

        chunk_size = payload.get("chunk_size")
        if chunk_size is not None and (
            not isinstance(chunk_size, int) or isinstance(chunk_size, bool)
        ):
            raise MalformedRequestError("chunk_size must be an int (not bool)")

        segment_gap = payload.get("segment_gap")
        if segment_gap is not None and (
            not isinstance(segment_gap, (int, float)) or isinstance(segment_gap, bool)
        ):
            raise MalformedRequestError("segment_gap must be int or float (not bool)")

        crossfade = payload.get("crossfade")
        if crossfade is not None and (
            not isinstance(crossfade, (int, float)) or isinstance(crossfade, bool)
        ):
            raise MalformedRequestError("crossfade must be int or float (not bool)")

        chunked = payload.get("chunked")
        if chunked is not None and not isinstance(chunked, bool):
            raise MalformedRequestError("chunked must be a bool")

        language = payload.get("language")
        if language is not None and not isinstance(language, str):
            raise MalformedRequestError("language must be a str")

        voice = payload.get("voice")
        if voice is not None and not isinstance(voice, str):
            raise MalformedRequestError("voice must be a str")

        accent = payload.get("accent")
        if accent is not None and not isinstance(accent, str):
            raise MalformedRequestError("accent must be a str")

        personality = payload.get("personality")
        if personality is not None and not isinstance(personality, str):
            raise MalformedRequestError("personality must be a str")

        emotion = payload.get("emotion")
        if emotion is not None and not isinstance(emotion, str):
            raise MalformedRequestError("emotion must be a str")

        trace_id = payload.get("trace_id")
        if trace_id is not None and not isinstance(trace_id, str):
            raise MalformedRequestError("trace_id must be a str")

        contract_version = payload.get("contract_version")
        if contract_version is not None and not isinstance(contract_version, str):
            raise MalformedRequestError("contract_version must be a str")

        fallback_language = payload.get("fallback_language")
        if fallback_language is not None and not isinstance(fallback_language, str):
            raise MalformedRequestError("fallback_language must be a str")

        return cls(
            text=text,
            request_id=request_id,
            trace_id=trace_id or "",
            contract_version=contract_version or "",
            engine=engine,
            language=language,
            voice=voice,
            speed=float(speed) if speed is not None else None,
            exaggeration=float(exaggeration) if exaggeration is not None else None,
            cfg_weight=float(cfg_weight) if cfg_weight is not None else None,
            accent=accent,
            personality=personality,
            emotion=emotion,
            chunked=chunked,
            chunk_size=chunk_size,
            segment_gap=float(segment_gap) if segment_gap is not None else None,
            crossfade=float(crossfade) if crossfade is not None else None,
            fallback_language=fallback_language,
        )
