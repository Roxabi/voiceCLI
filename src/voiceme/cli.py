from pathlib import Path
from typing import Annotated, Optional

import typer

from voiceme.engine import available_engines, get_engine
from voiceme.utils import build_output_prefix, default_output_path

app = typer.Typer(help="VoiceMe — unified voice generation CLI (Qwen3-TTS, Chatterbox & Chatterbox Turbo)")

# ── Samples sub-app ──────────────────────────────────────────────────────────

samples_app = typer.Typer(help="Manage voice samples")
app.add_typer(samples_app, name="samples")


@samples_app.command("list")
def samples_list():
    """List all samples in the samples/ directory."""
    from voiceme.samples import list_samples

    items = list_samples()
    if not items:
        typer.echo("No samples found. Use 'voiceme samples add <file>' to add one.")
        return
    for name in items:
        typer.echo(f"  {name}")


@samples_app.command("add")
def samples_add(
    file: Annotated[Path, typer.Argument(help="Path to a .wav file to import")],
):
    """Copy a local WAV file into the samples directory."""
    from voiceme.samples import add_sample

    try:
        dest = add_sample(file)
        typer.echo(f"Added {dest}")
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@samples_app.command("record")
def samples_record(
    name: Annotated[str, typer.Argument(help="Name for the recording (without .wav)")],
    duration: Annotated[float, typer.Option("--duration", "-d", help="Recording duration in seconds")] = 10.0,
):
    """Record audio from microphone and save as a sample."""
    from voiceme.samples import record_sample

    dest = record_sample(name, duration=duration)
    typer.echo(f"Recorded {dest}")


@samples_app.command("use")
def samples_use(
    name: Annotated[str, typer.Argument(help="Sample filename to set as active")],
):
    """Set a sample as the active reference for voice cloning."""
    from voiceme.samples import set_active

    try:
        set_active(name)
        typer.echo(f"Active sample set to: {name}")
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@samples_app.command("active")
def samples_active():
    """Show the currently active sample."""
    from voiceme.samples import get_active

    name = get_active()
    if name:
        typer.echo(f"Active sample: {name}")
    else:
        typer.echo("No active sample set. Use 'voiceme samples use <name>' to set one.")


@samples_app.command("remove")
def samples_remove(
    name: Annotated[str, typer.Argument(help="Sample filename to remove")],
):
    """Remove a sample from the samples directory."""
    from voiceme.samples import remove_sample

    try:
        remove_sample(name)
        typer.echo(f"Removed {name}")
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


# ── Core commands ────────────────────────────────────────────────────────────


@app.command()
def generate(
    text: Annotated[str, typer.Argument(help="Text to synthesize, or path to a .md file")],
    engine: Annotated[str, typer.Option("--engine", "-e", help="TTS engine")] = "qwen",
    voice: Annotated[Optional[str], typer.Option("--voice", "-v", help="Voice name")] = None,
    output: Annotated[
        Optional[Path], typer.Option("--output", "-o", help="Output WAV path")
    ] = None,
    language: Annotated[str, typer.Option("--lang", help="Language")] = "English",
    mp3: Annotated[bool, typer.Option("--mp3", help="Also save as MP3")] = False,
):
    """Generate speech from text or a markdown file using a built-in voice."""
    extra_kwargs: dict = {}
    script_stem: str | None = None

    # Detect .md file input
    text_path = Path(text)
    if text.endswith(".md") and text_path.exists():
        from voiceme.markdown import parse_md_file
        from voiceme.translate import translate_for_engine

        script_stem = text_path.stem
        doc = parse_md_file(text_path)
        # Frontmatter provides defaults; CLI flags override
        if doc.engine:
            engine = doc.engine
        doc = translate_for_engine(doc, engine)
        text = doc.text
        if doc.language:
            language = doc.language
        if doc.voice and voice is None:
            voice = doc.voice
        if doc.instruct:
            extra_kwargs["instruct"] = doc.instruct
        if doc.exaggeration is not None:
            extra_kwargs["exaggeration"] = doc.exaggeration
        if doc.cfg_weight is not None:
            extra_kwargs["cfg_weight"] = doc.cfg_weight
        if doc.segments and len(doc.segments) > 1:
            extra_kwargs["segments"] = doc.segments

    eng = get_engine(engine)
    prefix = build_output_prefix(engine, script=script_stem, voice=voice, language=language)
    out = output or default_output_path(prefix)
    result = eng.generate(text, voice, out, language=language, **extra_kwargs)
    typer.echo(f"Saved to {result}")
    if mp3:
        from voiceme.utils import wav_to_mp3

        mp3_path = wav_to_mp3(result)
        typer.echo(f"Saved to {mp3_path}")


@app.command()
def clone(
    text: Annotated[str, typer.Argument(help="Text to synthesize")],
    ref: Annotated[
        Optional[Path], typer.Option("--ref", "-r", help="Reference audio for voice cloning")
    ] = None,
    engine: Annotated[str, typer.Option("--engine", "-e", help="TTS engine")] = "qwen",
    ref_text: Annotated[
        Optional[str], typer.Option("--ref-text", help="Transcript of reference audio")
    ] = None,
    output: Annotated[
        Optional[Path], typer.Option("--output", "-o", help="Output WAV path")
    ] = None,
    language: Annotated[str, typer.Option("--lang", help="Language")] = "English",
    mp3: Annotated[bool, typer.Option("--mp3", help="Also save as MP3")] = False,
):
    """Clone a voice from reference audio and synthesize text."""
    extra_kwargs: dict = {}
    script_stem: str | None = None

    # Detect .md file input
    text_path = Path(text)
    if text.endswith(".md") and text_path.exists():
        from voiceme.markdown import parse_md_file
        from voiceme.translate import translate_for_engine

        script_stem = text_path.stem
        doc = parse_md_file(text_path)
        if doc.engine:
            engine = doc.engine
        doc = translate_for_engine(doc, engine)
        text = doc.text
        if doc.language:
            language = doc.language
        if doc.instruct:
            extra_kwargs["instruct"] = doc.instruct
        if doc.exaggeration is not None:
            extra_kwargs["exaggeration"] = doc.exaggeration
        if doc.cfg_weight is not None:
            extra_kwargs["cfg_weight"] = doc.cfg_weight
        if doc.segments and len(doc.segments) > 1:
            extra_kwargs["segments"] = doc.segments

    # Fall back to active sample if --ref not provided
    if ref is None:
        from voiceme.samples import get_active_path

        ref = get_active_path()
        if ref is None:
            typer.echo(
                "Error: no --ref provided and no active sample set.\n"
                "Use 'voiceme samples use <name>' to set an active sample.",
                err=True,
            )
            raise typer.Exit(1)
        typer.echo(f"Using active sample: {ref.name}")

    if not ref.exists():
        typer.echo(f"Error: reference audio not found: {ref}", err=True)
        raise typer.Exit(1)

    eng = get_engine(engine)
    prefix = build_output_prefix(engine, script=script_stem, language=language, clone=True)
    out = output or default_output_path(prefix)
    result = eng.clone(text, ref, out, ref_text=ref_text, language=language, **extra_kwargs)
    typer.echo(f"Saved to {result}")
    if mp3:
        from voiceme.utils import wav_to_mp3

        mp3_path = wav_to_mp3(result)
        typer.echo(f"Saved to {mp3_path}")


@app.command()
def transcribe(
    audio: Annotated[Path, typer.Argument(help="Audio file to transcribe")],
    model: Annotated[str, typer.Option("--model", "-m", help="Whisper model name")] = "large-v3-turbo",
    language: Annotated[Optional[str], typer.Option("--lang", "-l", help="Force language code")] = None,
    output: Annotated[Optional[Path], typer.Option("--output", "-o", help="Save text to file")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="JSON output with timestamps")] = False,
):
    """Transcribe speech from an audio file to text."""
    from voiceme.transcribe import transcribe as do_transcribe

    if not audio.exists():
        typer.echo(f"Error: file not found: {audio}", err=True)
        raise typer.Exit(1)

    result = do_transcribe(audio, model=model, language=language)

    if json_output:
        import json

        data = {"text": result.text, "language": result.language, "segments": result.segments}
        text_out = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        if result.language:
            typer.echo(f"[detected: {result.language}]", err=True)
        text_out = result.text

    if output:
        output.write_text(text_out, encoding="utf-8")
        typer.echo(f"Saved to {output}")
    else:
        typer.echo(text_out)


@app.command()
def listen(
    model: Annotated[str, typer.Option("--model", "-m", help="Kyutai model: 1b or 2.6b")] = "1b",
):
    """Live speech-to-text from microphone (Kyutai STT)."""
    from voiceme.listen import MODELS, listen_loop

    if model not in MODELS:
        typer.echo(f"Error: unknown model '{model}'. Choose from: {', '.join(MODELS)}", err=True)
        raise typer.Exit(1)
    listen_loop(model=model)


@app.command()
def mp3(
    file: Annotated[Path, typer.Argument(help="WAV file to convert")],
    bitrate: Annotated[int, typer.Option("--bitrate", "-b", help="MP3 bitrate in kbps")] = 192,
):
    """Convert a WAV file to MP3."""
    from voiceme.utils import wav_to_mp3

    if not file.exists():
        typer.echo(f"Error: file not found: {file}", err=True)
        raise typer.Exit(1)
    result = wav_to_mp3(file, bitrate=bitrate)
    typer.echo(f"Saved to {result}")


@app.command()
def voices(
    engine: Annotated[str, typer.Option("--engine", "-e", help="TTS engine")] = "qwen",
):
    """List available voices for an engine."""
    eng = get_engine(engine)
    for v in eng.list_voices():
        typer.echo(f"  {v}")


@app.command()
def engines():
    """List available TTS engines."""
    for name in available_engines():
        typer.echo(f"  {name}")


@app.command()
def emotions():
    """Show available emotion/expressiveness controls for each engine."""
    typer.echo(
        """Emotion & Expressiveness Controls
==================================

Qwen (instruct mode)
--------------------
Use the 'instruct' field in .md frontmatter or pass via script:
  instruct: "Speak angrily"
  instruct: "Whispering"
  instruct: "With excitement"
  instruct: "Parle avec un ton chaleureux et amical"
Any free-form text instruction works.

Chatterbox Turbo (paralinguistic tags — English only)
-----------------------------------------------------
Insert tags directly in your text (engine: chatterbox-turbo):
  [laugh]  [chuckle]  [cough]  [sigh]
  [gasp]   [groan]    [sniff]  [shush]
  [clear throat]

Chatterbox (numeric controls — both turbo & multilingual)
---------------------------------------------------------
Set in .md frontmatter or as kwargs:
  exaggeration: 0.25 - 2.0  (expressiveness, default 0.5)
  cfg_weight:   0.0  - 1.0  (speaker adherence, default 0.5)

Chatterbox Multilingual (23 languages)
--------------------------------------
  engine: chatterbox     — supports language field
  No paralinguistic tags — use exaggeration/cfg_weight for expressiveness
"""
    )
