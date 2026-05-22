"""Chunked-output helpers for `voicecli.api`.

Extracted from `api.py` (which was over 1000 LOC) to keep the public-API
module focused on orchestration. Used by both `generate` and `clone` paths.
"""

from __future__ import annotations

from pathlib import Path


def emit_chunk(
    eng,
    method: str,
    text: str,
    voice,
    out: Path,
    index: int,
    total: int,
    *,
    mp3: bool = False,
    daemon_fn=None,
    **kwargs,
) -> Path:
    """Generate and save a single numbered chunk. Returns chunk path."""
    stem = out.stem
    chunk_path = out.parent / f"{stem}_{index:03d}.wav"
    if daemon_fn is None or not daemon_fn(method, text, voice, chunk_path, **kwargs):
        if method == "generate":
            eng.generate(text, voice, chunk_path, **kwargs)
        else:
            eng.clone(
                text,
                kwargs.pop("ref_audio"),
                chunk_path,
                ref_text=kwargs.pop("ref_text", None),
                **kwargs,
            )
    if mp3:
        from voicecli.utils import wav_to_mp3

        wav_to_mp3(chunk_path)
    return chunk_path


def write_done(out: Path) -> Path:
    done_path = out.with_suffix(".done")
    done_path.write_text("done\n")
    return done_path


def generate_chunked(
    eng,
    text,
    voice,
    out,
    language,
    extra_kwargs,
    *,
    chunk_size,
    segments,
    mp3=False,
    daemon_fn=None,
) -> list[Path]:
    """Generate speech in chunks. Returns list of chunk paths."""
    from voicecli.utils import smart_chunk

    paths: list[Path] = []

    if segments and len(segments) > 1:
        total = len(segments)
        for i, seg in enumerate(segments, 1):
            kw = {**extra_kwargs, "language": seg.language or language}
            if seg.instruct:
                kw["instruct"] = seg.instruct
            if seg.exaggeration is not None:
                kw["exaggeration"] = seg.exaggeration
            if seg.cfg_weight is not None:
                kw["cfg_weight"] = seg.cfg_weight
            if seg.flow_steps is not None:
                kw["flow_steps"] = seg.flow_steps
            if seg.cfg_alpha is not None:
                kw["cfg_alpha"] = seg.cfg_alpha
            if seg.temperature is not None:
                kw["temperature"] = seg.temperature
            if seg.top_p is not None:
                kw["top_p"] = seg.top_p
            if seg.min_p is not None:
                kw["min_p"] = seg.min_p
            if seg.repetition_penalty is not None:
                kw["repetition_penalty"] = seg.repetition_penalty
            seg_voice = seg.voice or voice
            p = emit_chunk(
                eng,
                "generate",
                seg.text,
                seg_voice,
                out,
                i,
                total,
                mp3=mp3,
                daemon_fn=daemon_fn,
                **kw,
            )
            paths.append(p)
    else:
        chunks = smart_chunk(text, chunk_size)
        total = len(chunks)
        for i, chunk_text in enumerate(chunks, 1):
            p = emit_chunk(
                eng,
                "generate",
                chunk_text,
                voice,
                out,
                i,
                total,
                mp3=mp3,
                daemon_fn=daemon_fn,
                language=language,
                **extra_kwargs,
            )
            paths.append(p)

    write_done(out)
    return paths


def clone_chunked(
    eng,
    text,
    ref,
    ref_text,
    out,
    language,
    extra_kwargs,
    *,
    chunk_size,
    segments,
    mp3=False,
    daemon_fn=None,
) -> list[Path]:
    """Clone voice in chunks. Returns list of chunk paths."""
    from voicecli.utils import smart_chunk

    paths: list[Path] = []

    if segments and len(segments) > 1:
        total = len(segments)
        for i, seg in enumerate(segments, 1):
            kw = {
                **extra_kwargs,
                "language": seg.language or language,
                "ref_audio": ref,
                "ref_text": ref_text,
            }
            if seg.exaggeration is not None:
                kw["exaggeration"] = seg.exaggeration
            if seg.cfg_weight is not None:
                kw["cfg_weight"] = seg.cfg_weight
            if seg.flow_steps is not None:
                kw["flow_steps"] = seg.flow_steps
            if seg.cfg_alpha is not None:
                kw["cfg_alpha"] = seg.cfg_alpha
            if seg.temperature is not None:
                kw["temperature"] = seg.temperature
            if seg.top_p is not None:
                kw["top_p"] = seg.top_p
            if seg.min_p is not None:
                kw["min_p"] = seg.min_p
            if seg.repetition_penalty is not None:
                kw["repetition_penalty"] = seg.repetition_penalty
            p = emit_chunk(
                eng,
                "clone",
                seg.text,
                None,
                out,
                i,
                total,
                mp3=mp3,
                daemon_fn=daemon_fn,
                **kw,
            )
            paths.append(p)
    else:
        chunks = smart_chunk(text, chunk_size)
        total = len(chunks)
        for i, chunk_text in enumerate(chunks, 1):
            p = emit_chunk(
                eng,
                "clone",
                chunk_text,
                None,
                out,
                i,
                total,
                mp3=mp3,
                daemon_fn=daemon_fn,
                language=language,
                ref_audio=ref,
                ref_text=ref_text,
                **extra_kwargs,
            )
            paths.append(p)

    write_done(out)
    return paths
