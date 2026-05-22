"""Coverage for voicecli.api_chunked — the extracted chunked-output helpers (audit #162)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from voicecli.api.chunked import clone_chunked, generate_chunked, write_done


def test_generate_chunked_returns_three_chunk_paths(tmp_path):
    out_path = tmp_path / "out.wav"
    eng = MagicMock()
    eng.generate.side_effect = lambda text, voice, p, **kw: p.write_bytes(b"\x00") or p

    with patch("voicecli.utils.smart_chunk", return_value=["a", "b", "c"]):
        paths = generate_chunked(
            eng,
            "abc",
            voice=None,
            out=out_path,
            language="English",
            extra_kwargs={},
            chunk_size=10,
            segments=None,
        )

    assert len(paths) == 3
    assert [p.name for p in paths] == ["out_001.wav", "out_002.wav", "out_003.wav"]
    assert eng.generate.call_count == 3
    # .done sentinel is written by write_done at the end
    assert out_path.with_suffix(".done").exists()


def test_clone_chunked_returns_three_chunk_paths(tmp_path):
    out_path = tmp_path / "out.wav"
    ref_audio = tmp_path / "ref.wav"
    ref_audio.write_bytes(b"\x00" * 100)

    eng = MagicMock()
    eng.clone.side_effect = lambda text, ref, p, ref_text=None, **kw: p.write_bytes(b"\x00") or p

    with patch("voicecli.utils.smart_chunk", return_value=["a", "b", "c"]):
        paths = clone_chunked(
            eng,
            "abc",
            ref=ref_audio,
            ref_text=None,
            out=out_path,
            language="English",
            extra_kwargs={},
            chunk_size=10,
            segments=None,
        )

    assert len(paths) == 3
    assert [p.name for p in paths] == ["out_001.wav", "out_002.wav", "out_003.wav"]
    assert eng.clone.call_count == 3
    # Each call must receive ref_audio (positional) — guards against ref drop on extraction
    for call in eng.clone.call_args_list:
        assert call.args[1] == ref_audio
    assert out_path.with_suffix(".done").exists()


def test_write_done_creates_sentinel(tmp_path):
    out_path = tmp_path / "x.wav"
    done = write_done(out_path)
    assert done == out_path.with_suffix(".done")
    assert done.read_text() == "done\n"
