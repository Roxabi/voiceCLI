"""Tests for ChatterboxBase._segment_kwargs delta-injection hook (audit #162)."""

from __future__ import annotations

from voicecli.engines.chatterbox import ChatterboxEngine
from voicecli.engines.chatterbox_turbo import ChatterboxTurboEngine
from voicecli.api.markdown_types import Segment


def _seg(language: str | None = None) -> Segment:
    return Segment(text="hello", language=language)


def test_multilingual_chatterbox_segment_kwargs_injects_language_id():
    eng = ChatterboxEngine()
    out = eng._segment_kwargs(_seg(language="French"))
    assert "language_id" in out
    assert out["language_id"] is not None


def test_multilingual_chatterbox_segment_kwargs_empty_when_no_language():
    eng = ChatterboxEngine()
    assert eng._segment_kwargs(_seg(language=None)) == {}


def test_turbo_chatterbox_segment_kwargs_always_empty():
    """Turbo is English-only; the base default returns {} regardless of seg.language."""
    eng = ChatterboxTurboEngine()
    assert eng._segment_kwargs(_seg(language="French")) == {}
    assert eng._segment_kwargs(_seg(language=None)) == {}
