"""API generate — speech synthesis from text or markdown scripts."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voicecli.ports.synthesis import SynthesisPort

from voicecli.api.chunked import generate_chunked
from voicecli.api.config import _resolve_config
from voicecli.api.input import _resolve_input
from voicecli.api.utils import TTSResult
from voicecli.api.validation import _validate_output_path, _validate_tts_params
from voicecli.core.utils import OUTPUT_DIR, _Unrestricted

log = logging.getLogger(__name__)


def generate(
    text: str | Path,
    *,
    engine: str | None = None,
    voice: str | None = None,
    output: str | Path | None = None,
    language: str | None = None,
    mp3: bool = False,
    fast: bool = False,
    chunked: bool = False,
    chunk_size: int | None = None,
    config: str | Path | None = None,
    segment_gap: int | None = None,
    crossfade: int | None = None,
    plain: bool = False,
    allowed_base: Path | _Unrestricted = OUTPUT_DIR,
    _synthesis: "SynthesisPort | None" = None,
    **kwargs,
) -> TTSResult:
    """Generate speech from text or a markdown file using a built-in voice."""
    _validate_tts_params(
        text=text,
        engine=engine,
        voice=voice,
        language=language,
        segment_gap=segment_gap,
        crossfade=crossfade,
        chunk_size=chunk_size,
        extra_kwargs=kwargs,
    )

    from voicecli.engines.engine import QWEN_ENGINES
    from voicecli.core.utils import build_output_prefix, default_output_path

    if _synthesis is None:
        from voicecli.adapters.synthesis import DaemonSynthesisAdapter  # type: ignore[import-not-found]

        _synthesis = DaemonSynthesisAdapter()
    assert _synthesis is not None

    config_path = Path(config) if config is not None else None

    resolved = _resolve_config(
        engine=engine,
        language=language,
        voice=voice,
        mp3=mp3,
        fast=fast,
        chunked=chunked,
        chunk_size=chunk_size,
        plain=plain,
        segment_gap=segment_gap,
        crossfade=crossfade,
        config=config_path,
        extra_kwargs=kwargs,
    )
    resolved["_segment_gap_from_caller"] = segment_gap
    resolved["_crossfade_from_caller"] = crossfade

    resolved = _resolve_input(text, resolved)

    r_engine = resolved["engine"]
    r_text = resolved["text"]
    r_language = resolved["language"]
    r_voice = resolved["voice"]
    r_chunked = resolved["chunked"]
    r_chunk_size = resolved["chunk_size"]
    r_fast = resolved["fast"]
    r_mp3 = resolved["mp3"]
    script_stem = resolved["script_stem"]
    extra = resolved["extra_kwargs"]

    prefix = build_output_prefix(r_engine, script=script_stem, voice=r_voice, language=r_language)
    if output is not None:
        out = _validate_output_path(Path(output), allowed_base=allowed_base)
    else:
        out = default_output_path(prefix)

    if r_chunked:
        chunk_fn = getattr(_synthesis, "_chunk_fn", None)
        daemon_fn = (
            chunk_fn(r_engine) if r_engine in QWEN_ENGINES and chunk_fn is not None else None
        )
        registry = getattr(_synthesis, "_registry", None)
        if registry is not None:
            eng = registry.get(r_engine)
        else:
            from voicecli.engines.engine import get_engine

            eng = get_engine(r_engine)
        if r_fast and r_engine in QWEN_ENGINES:
            eng.set_small_mode()  # pyright: ignore[reportAttributeAccessIssue]
        chunk_paths = generate_chunked(
            eng,
            r_text,
            r_voice,
            out,
            r_language,
            extra,
            chunk_size=r_chunk_size,
            segments=extra.pop("segments", None),
            mp3=r_mp3,
            daemon_fn=daemon_fn,
        )
        return TTSResult(wav_path=out.with_suffix(".done"), chunk_paths=chunk_paths)

    result = _synthesis.generate(
        r_engine,
        r_text,
        r_voice,
        out,
        language=r_language,
        fast=r_fast,
        **extra,
    )
    if result is not None:
        out = result
        mp3_path = None
        if r_mp3:
            from voicecli.core.utils import wav_to_mp3

            mp3_path = wav_to_mp3(out)
        return TTSResult(wav_path=out, mp3_path=mp3_path)

    raise RuntimeError(f"Synthesis failed for engine '{r_engine}': adapter returned None")


async def generate_async(*args, **kwargs) -> TTSResult:
    """Async wrapper for generate() — runs in a thread for event loop integration."""
    return await asyncio.to_thread(generate, *args, **kwargs)
