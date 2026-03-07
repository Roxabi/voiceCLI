"""Tests for the voicecli public library API."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_import_lightweight():
    """Importing voicecli should NOT trigger heavy imports (torch, soundfile, etc.)."""
    # Unload voicecli modules to test fresh import
    mods_to_remove = [k for k in sys.modules if k.startswith("voicecli")]
    saved = {k: sys.modules.pop(k) for k in mods_to_remove}
    try:
        import voicecli  # noqa: F811

        assert "torch" not in sys.modules
        assert "soundfile" not in sys.modules
        assert "faster_whisper" not in sys.modules
        assert hasattr(voicecli, "generate")
        assert hasattr(voicecli, "clone")
        assert hasattr(voicecli, "TTSResult")
    finally:
        # Restore modules
        sys.modules.update(saved)


def test_generate_returns_tts_result(tmp_path):
    """generate() should return a TTSResult with a valid wav_path."""
    from voicecli.api import TTSResult, generate

    mock_engine = MagicMock()
    out_path = tmp_path / "test.wav"
    mock_engine.generate.return_value = out_path

    with (
        patch("voicecli.engine.get_engine", return_value=mock_engine),
        patch("voicecli.api._try_daemon", return_value=None),
        patch("voicecli.config.load_defaults", return_value={}),
    ):
        result = generate("Hello world", output=out_path)

    assert isinstance(result, TTSResult)
    assert result.wav_path == out_path
    assert result.mp3_path is None


def test_generate_md_input(tmp_path):
    """generate() with a .md file should process frontmatter and segments."""
    from voicecli.api import TTSResult, generate

    md_file = tmp_path / "test.md"
    md_file.write_text("---\nlanguage: French\n---\nBonjour le monde.")

    mock_engine = MagicMock()
    out_path = tmp_path / "out.wav"
    mock_engine.generate.return_value = out_path

    with (
        patch("voicecli.engine.get_engine", return_value=mock_engine),
        patch("voicecli.api._try_daemon", return_value=None),
        patch("voicecli.config.load_defaults", return_value={}),
    ):
        result = generate(str(md_file), output=out_path)

    assert isinstance(result, TTSResult)
    mock_engine.generate.assert_called_once()
    call_kwargs = mock_engine.generate.call_args
    assert call_kwargs[1].get("language") == "French" or call_kwargs[0][3] is not None


def test_clone_no_ref_no_active_raises_valueerror():
    """clone() with no ref and no active sample should raise ValueError."""
    from voicecli.api import clone

    with (
        patch("voicecli.samples.get_active_path", return_value=None),
        pytest.raises(ValueError, match="no active sample"),
    ):
        clone("Hello")


def test_invalid_engine_raises_valueerror():
    """generate() with an invalid engine name should raise ValueError."""
    from voicecli.api import generate

    with (
        patch("voicecli.config.load_defaults", return_value={}),
        pytest.raises(ValueError, match="Unknown engine"),
    ):
        generate("Hello", engine="nonexistent-engine")


def test_missing_ref_raises_filenotfounderror():
    """clone() with a non-existent ref path should raise FileNotFoundError."""
    from voicecli.api import clone

    with pytest.raises(FileNotFoundError, match="Reference audio not found"):
        clone("Hello", ref="/nonexistent/audio.wav")


def test_path_params_accept_str_and_path(tmp_path):
    """All path parameters should accept both str and Path."""
    from voicecli.api import TTSResult, generate

    mock_engine = MagicMock()
    out_path = tmp_path / "test.wav"
    mock_engine.generate.return_value = out_path

    with (
        patch("voicecli.engine.get_engine", return_value=mock_engine),
        patch("voicecli.api._try_daemon", return_value=None),
        patch("voicecli.config.load_defaults", return_value={}),
    ):
        # str output
        result = generate("Hello", output=str(out_path))
        assert isinstance(result, TTSResult)

        # Path output
        result = generate("Hello", output=out_path)
        assert isinstance(result, TTSResult)


def test_list_engines_returns_strings():
    """list_engines() should return a list of engine key strings."""
    from voicecli.api import list_engines

    engines = list_engines()
    assert isinstance(engines, list)
    assert len(engines) > 0
    assert all(isinstance(e, str) for e in engines)
    assert "qwen" in engines


def test_list_voices_invalid_raises_valueerror():
    """list_voices() with an invalid engine should raise ValueError."""
    from voicecli.api import list_voices

    with pytest.raises(ValueError, match="Unknown engine"):
        list_voices("nonexistent-engine")


def test_cuda_guard_raises_runtimeerror():
    """cuda_guard should raise RuntimeError, not SystemExit."""
    from voicecli.engine import cuda_guard

    with pytest.raises(RuntimeError, match="CUDA error"):
        with cuda_guard("test"):
            raise RuntimeError("CUDA out of memory")


def test_cuda_guard_passes_non_cuda_errors():
    """cuda_guard should not catch non-CUDA RuntimeErrors."""
    from voicecli.engine import cuda_guard

    with pytest.raises(RuntimeError, match="some other error"):
        with cuda_guard("test"):
            raise RuntimeError("some other error")


def test_exports():
    """All documented exports should be importable from voicecli."""
    from voicecli import (
        Segment,
        TTSDocument,
        TTSResult,
        TranscriptionResult,
        __version__,
        clone,
        clone_async,
        generate,
        generate_async,
        list_engines,
        list_voices,
        transcribe,
        transcribe_async,
    )

    assert __version__
    assert callable(generate)
    assert callable(clone)
    assert callable(transcribe)
    assert callable(generate_async)
    assert callable(clone_async)
    assert callable(transcribe_async)
    assert callable(list_engines)
    assert callable(list_voices)
    assert TTSResult is not None
    assert TranscriptionResult is not None
    assert TTSDocument is not None
    assert Segment is not None
