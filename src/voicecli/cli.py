import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from voicecli import __version__
from voicecli.engine import QWEN_ENGINES, available_engines, get_engine


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"voicecli {__version__}")
        raise typer.Exit()


app = typer.Typer(
    help="VoiceCLI — unified voice generation CLI (Qwen3-TTS, Chatterbox & Chatterbox Turbo)"
)


@app.callback()
def _main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Show version and exit",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """VoiceCLI — unified voice generation CLI."""


# ── Samples sub-app ──────────────────────────────────────────────────────────

samples_app = typer.Typer(help="Manage voice samples")
app.add_typer(samples_app, name="samples")


@samples_app.command("list")
def samples_list():
    """List all samples in the TTS/samples/ directory."""
    from voicecli.samples import list_samples

    items = list_samples()
    if not items:
        typer.echo("No samples found. Use 'voicecli samples add <file>' to add one.")
        return
    for name in items:
        typer.echo(f"  {name}")


@samples_app.command("add")
def samples_add(
    file: Annotated[Path, typer.Argument(help="Path to a .wav file to import")],
):
    """Copy a local WAV file into the samples directory."""
    from voicecli.samples import add_sample

    try:
        dest = add_sample(file)
        typer.echo(f"Added {dest}")
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@samples_app.command("record")
def samples_record(
    name: Annotated[str, typer.Argument(help="Name for the recording (without .wav)")],
    duration: Annotated[
        float, typer.Option("--duration", "-d", help="Recording duration in seconds")
    ] = 10.0,
):
    """Record audio from microphone and save as a sample."""
    from voicecli.samples import record_sample

    dest = record_sample(name, duration=duration)
    typer.echo(f"Recorded {dest}")


@samples_app.command("use")
def samples_use(
    name: Annotated[str, typer.Argument(help="Sample filename to set as active")],
):
    """Set a sample as the active reference for voice cloning."""
    from voicecli.samples import set_active

    try:
        set_active(name)
        typer.echo(f"Active sample set to: {name}")
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@samples_app.command("active")
def samples_active():
    """Show the currently active sample."""
    from voicecli.samples import get_active

    name = get_active()
    if name:
        typer.echo(f"Active sample: {name}")
    else:
        typer.echo("No active sample set. Use 'voicecli samples use <name>' to set one.")


@samples_app.command("remove")
def samples_remove(
    name: Annotated[str, typer.Argument(help="Sample filename to remove")],
):
    """Remove a sample from the samples directory."""
    from voicecli.samples import remove_sample

    try:
        remove_sample(name)
        typer.echo(f"Removed {name}")
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@samples_app.command("from-url")
def samples_from_url(
    url: Annotated[str, typer.Argument(help="YouTube (or other yt-dlp supported) URL")],
    name: Annotated[str, typer.Argument(help="Name for the sample (without .wav)")],
    start: Annotated[
        float, typer.Option("--start", "-s", help="Start time in seconds (skip intro)")
    ] = 10.0,
    duration: Annotated[float, typer.Option("--duration", "-d", help="Duration in seconds")] = 30.0,
    use: Annotated[bool, typer.Option("--use", help="Set as active sample after download")] = False,
):
    """Download audio from a URL, extract and normalize a voice sample."""
    from voicecli.samples import from_url, set_active

    try:
        dest = from_url(url, name, start=start, duration=duration)
        typer.echo(f"Added {dest}")
        if use:
            wav_name = dest.name
            set_active(wav_name)
            typer.echo(f"Active sample set to: {wav_name}")
    except (RuntimeError, ValueError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


# ── Dictate sub-app ───────────────────────────────────────────────────────────

dictate_app = typer.Typer(help="Dictation client for STT daemon")
app.add_typer(dictate_app, name="dictate", invoke_without_command=True)


@dictate_app.callback(invoke_without_command=True)
def dictate(
    ctx: typer.Context,
    listen: Annotated[
        bool,
        typer.Option("--listen", help="Start global hotkey listener (blocks until Ctrl+C)"),
    ] = False,
    paste: Annotated[
        bool,
        typer.Option("--paste", help="Auto-type transcribed text into the focused window"),
    ] = False,
) -> None:
    """Toggle dictation recording or start the hotkey listener."""
    if ctx.invoked_subcommand is not None:
        return

    if listen:
        from voicecli.config import load_stt_config
        from voicecli.stt_client import hotkey_loop

        stt_cfg = load_stt_config()
        hotkey_loop(stt_cfg["hotkey"], paste=paste)
        return

    from voicecli.stt_client import auto_paste, notify, send_toggle

    resp = send_toggle()

    if resp.get("status") == "error":
        print(resp.get("message", "unknown error"), file=sys.stderr)
        notify(resp.get("message", "STT daemon not running"), timeout=3000)
        raise typer.Exit(code=1)

    state = resp.get("state", "")
    text = resp.get("text", "")
    language = resp.get("language") or ""

    if state == "recording":
        print(state)
        notify("Recording...", timeout=0)
    elif state == "idle" and text:
        print(text)
        preview = text[:50] + ("..." if len(text) > 50 else "")
        lang_tag = f"[{language}] " if language else ""
        notify(f"{lang_tag}{preview}", timeout=3000)
        if paste:
            auto_paste(text)
    elif state == "queued":
        print(state)
        notify("Queued...", timeout=3000)
    else:
        print(state)


@dictate_app.command("status")
def dictate_status() -> None:
    """Show current STT daemon state."""
    from voicecli.stt_client import send_status

    resp = send_status()
    if resp.get("status") == "error":
        print(resp.get("message", "unknown error"), file=sys.stderr)
        raise typer.Exit(code=1)
    print(resp.get("state", "unknown"))


# ── CUDA error formatting (moved from engine.py cuda_guard) ──────────────────


def _print_cuda_error(msg: str) -> None:
    """Format a CUDA RuntimeError into a user-friendly diagnostic."""
    print(f"\n{'=' * 60}")
    print(f"  {msg.split(':')[0] if ':' in msg else 'CUDA error'}")
    print(f"{'=' * 60}")
    detail = msg.split(":", 1)[1].strip() if ":" in msg else msg
    lower = detail.lower()
    if "out of memory" in lower:
        print("\n  Your GPU does not have enough VRAM for this model.")
        print("  Try closing other GPU-intensive apps first.")
    elif "no kernel image" in lower or "not compiled" in lower:
        print("\n  PyTorch was not compiled for your GPU architecture.")
        print("  Reinstall PyTorch matching your CUDA version:")
        print("    https://pytorch.org/get-started/locally/")
    else:
        print(f"\n  {detail[:200]}")
    print("\n  Troubleshooting:")
    print("    1. Check drivers: nvidia-smi")
    print("    2. Check CUDA toolkit: nvcc --version")
    print("    3. Run: voicecli doctor")
    print(f"{'=' * 60}\n")


# ── Core commands ────────────────────────────────────────────────────────────


@app.command()
def generate(
    text: Annotated[str, typer.Argument(help="Text to synthesize, or path to a .md file")],
    engine: Annotated[
        Optional[str], typer.Option("--engine", "-e", help="TTS engine", show_default="qwen")
    ] = None,
    voice: Annotated[Optional[str], typer.Option("--voice", "-v", help="Voice name")] = None,
    output: Annotated[
        Optional[Path], typer.Option("--output", "-o", help="Output WAV path")
    ] = None,
    language: Annotated[
        Optional[str], typer.Option("--lang", help="Language", show_default="English")
    ] = None,
    mp3: Annotated[bool, typer.Option("--mp3", help="Also save as MP3")] = False,
    fast: Annotated[
        bool, typer.Option("--fast", help="Use smaller 0.6B model (faster, lower quality)")
    ] = False,
    segment_gap: Annotated[
        Optional[int], typer.Option("--segment-gap", help="Silence between segments (ms)")
    ] = None,
    crossfade: Annotated[
        Optional[int], typer.Option("--crossfade", help="Fade between segments (ms)")
    ] = None,
    chunked: Annotated[
        bool,
        typer.Option(
            "--chunked", help="Output each chunk as a separate file for progressive sending"
        ),
    ] = False,
    chunk_size: Annotated[
        Optional[int],
        typer.Option(
            "--chunk-size", help="Target chunk size in characters (~15 chars/sec of speech)"
        ),
    ] = None,
    plain: Annotated[
        bool,
        typer.Option(
            "--plain", help="Ignore [tags] and <!-- directives -->, generate flat text only"
        ),
    ] = False,
    config: Annotated[
        Optional[Path],
        typer.Option("--config", help="Explicit path to voicecli.toml (overrides walk-up search)"),
    ] = None,
):
    """Generate speech from text or a markdown file using a built-in voice."""
    from voicecli.api import generate as api_generate

    try:
        result = api_generate(
            text,
            engine=engine,
            voice=voice,
            output=output,
            language=language,
            mp3=mp3,
            fast=fast,
            chunked=chunked,
            chunk_size=chunk_size,
            config=config,
            segment_gap=segment_gap,
            crossfade=crossfade,
            plain=plain,
        )
        typer.echo(f"Saved to {result.wav_path}")
        if result.mp3_path:
            typer.echo(f"Saved to {result.mp3_path}")
    except (ValueError, FileNotFoundError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except RuntimeError as e:
        _print_cuda_error(str(e))
        raise typer.Exit(1)


@app.command()
def clone(
    text: Annotated[str, typer.Argument(help="Text to synthesize")],
    ref: Annotated[
        Optional[Path], typer.Option("--ref", "-r", help="Reference audio for voice cloning")
    ] = None,
    engine: Annotated[
        Optional[str], typer.Option("--engine", "-e", help="TTS engine", show_default="qwen")
    ] = None,
    ref_text: Annotated[
        Optional[str], typer.Option("--ref-text", help="Transcript of reference audio")
    ] = None,
    output: Annotated[
        Optional[Path], typer.Option("--output", "-o", help="Output WAV path")
    ] = None,
    language: Annotated[
        Optional[str], typer.Option("--lang", help="Language", show_default="English")
    ] = None,
    mp3: Annotated[bool, typer.Option("--mp3", help="Also save as MP3")] = False,
    fast: Annotated[
        bool, typer.Option("--fast", help="Use smaller 0.6B model (faster, lower quality)")
    ] = False,
    segment_gap: Annotated[
        Optional[int], typer.Option("--segment-gap", help="Silence between segments (ms)")
    ] = None,
    crossfade: Annotated[
        Optional[int], typer.Option("--crossfade", help="Fade between segments (ms)")
    ] = None,
    chunked: Annotated[
        bool,
        typer.Option(
            "--chunked", help="Output each chunk as a separate file for progressive sending"
        ),
    ] = False,
    chunk_size: Annotated[
        Optional[int],
        typer.Option(
            "--chunk-size", help="Target chunk size in characters (~15 chars/sec of speech)"
        ),
    ] = None,
    plain: Annotated[
        bool,
        typer.Option(
            "--plain", help="Ignore [tags] and <!-- directives -->, generate flat text only"
        ),
    ] = False,
    config: Annotated[
        Optional[Path],
        typer.Option("--config", help="Explicit path to voicecli.toml (overrides walk-up search)"),
    ] = None,
):
    """Clone a voice from reference audio and synthesize text."""
    from voicecli.api import clone as api_clone

    try:
        result = api_clone(
            text,
            ref=ref,
            engine=engine,
            ref_text=ref_text,
            output=output,
            language=language,
            mp3=mp3,
            fast=fast,
            chunked=chunked,
            chunk_size=chunk_size,
            config=config,
            segment_gap=segment_gap,
            crossfade=crossfade,
            plain=plain,
        )
        typer.echo(f"Saved to {result.wav_path}")
        if result.mp3_path:
            typer.echo(f"Saved to {result.mp3_path}")
    except (ValueError, FileNotFoundError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except RuntimeError as e:
        _print_cuda_error(str(e))
        raise typer.Exit(1)


@app.command()
def transcribe(
    audio: Annotated[Path, typer.Argument(help="Audio file to transcribe")],
    model: Annotated[
        str, typer.Option("--model", "-m", help="Whisper model name")
    ] = "large-v3-turbo",
    language: Annotated[
        Optional[str], typer.Option("--lang", "-l", help="Force language code")
    ] = None,
    output: Annotated[
        Optional[Path], typer.Option("--output", "-o", help="Save text to file")
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="JSON output with timestamps")
    ] = False,
):
    """Transcribe speech from an audio file to text."""
    from voicecli.transcribe import transcribe as do_transcribe

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

    typer.echo(text_out)

    if output is None:
        from voicecli.utils import default_output_path

        ext = "json" if json_output else "txt"
        output = default_output_path(prefix=audio.stem, fmt=ext, base_dir=Path("STT/texts_out"))

    output.write_text(text_out, encoding="utf-8")
    typer.echo(f"Saved to {output}", err=True)


@app.command()
def listen(
    model: Annotated[str, typer.Option("--model", "-m", help="Kyutai model: 1b or 2.6b")] = "1b",
):
    """Live speech-to-text from microphone (Kyutai STT)."""
    from voicecli.listen import MODELS, listen_loop

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
    from voicecli.utils import wav_to_mp3

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
def init(
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip prompts, write default template")
    ] = False,
):
    """Create a voicecli.toml config file (interactive wizard or -y for defaults)."""
    config_path = Path("voicecli.toml")
    if config_path.exists():
        typer.echo("voicecli.toml already exists — not overwriting.")
        raise typer.Exit(1)

    if yes:
        template = """\
[defaults]
# language = "French"
# engine = "qwen"                # qwen | chatterbox | chatterbox-turbo
# voice = "Ryan"                 # built-in voice (Qwen only)

# ── Structured instruct parts (Qwen) ──
# These auto-compose into instruct: "accent. personality. speed. emotion"
# Write them in the target language.
# accent = "Light southern French accent"
# personality = "Calm, warm and articulate"
# speed = "Measured pace with natural pauses"
# emotion = "Warm and engaged"

# ── Chatterbox expressiveness ──
# exaggeration = 0.5             # 0.25-2.0 (default 0.5)
# cfg_weight = 0.5               # 0.0-1.0 (default 0.5)

# ── Segment transitions ──
# segment_gap = 0                # ms silence between segments
# crossfade = 0                  # ms fade between segments
"""
        config_path.write_text(template)
        typer.echo("Created voicecli.toml — edit it to set your defaults.")
        _offer_path_install()
        return

    # ── Interactive wizard ──────────────────────────────────────────────────
    typer.echo("VoiceCLI config wizard — press Enter to accept defaults.\n")
    values: dict[str, object] = {}
    valid_engines = available_engines()

    # 1. Engine
    typer.echo(f"  Available engines: {', '.join(valid_engines)}")
    while True:
        engine = typer.prompt("Engine", default="qwen").strip()
        if engine in valid_engines:
            break
        typer.echo(f"  Invalid engine. Choose from: {', '.join(valid_engines)}")
    if engine != "qwen":
        values["engine"] = engine

    # 2. Language (skip for chatterbox-turbo — English only)
    if engine != "chatterbox-turbo":
        from voicecli.utils import LANG_MAP

        lang_names = sorted({k.title() for k in LANG_MAP if k.isascii()})
        typer.echo(f"  Supported languages: {', '.join(lang_names)}")
        language = typer.prompt("Language", default="English").strip()
        if language.lower() != "english":
            values["language"] = language

    # 3. Voice (skip if engine only has "default")
    voices = _list_voices_for_engine(engine)
    if voices != ["default"]:
        typer.echo(f"  Available voices: {', '.join(voices)}")
        while True:
            voice = typer.prompt("Voice", default=voices[0]).strip()
            if voice in voices:
                break
            typer.echo(f"  Invalid voice. Choose from: {', '.join(voices)}")
        if voice != voices[0]:
            values["voice"] = voice

    # 4. Structured instruct parts (Qwen engines only)
    if engine.startswith("qwen"):
        typer.echo("\n  Structured instruct parts (compose into instruct string).")
        typer.echo("  Write them in the target language. Leave blank to skip.\n")
        for field, example in [
            ("accent", "e.g. Light southern French accent"),
            ("personality", "e.g. Calm, warm and articulate"),
            ("speed", "e.g. Measured pace with natural pauses"),
            ("emotion", "e.g. Warm and engaged"),
        ]:
            typer.echo(f"  {field} — {example}")
            val = typer.prompt(f"  {field}", default="").strip()
            if val:
                values[field] = val

    # 5. Chatterbox params
    if engine.startswith("chatterbox"):
        typer.echo("")
        values["exaggeration"] = _prompt_float("Exaggeration", 0.5, 0.25, 2.0)
        values["cfg_weight"] = _prompt_float("CFG weight", 0.5, 0.0, 1.0)
        # Remove if user kept the default
        if values["exaggeration"] == 0.5:
            del values["exaggeration"]
        if values["cfg_weight"] == 0.5:
            del values["cfg_weight"]

    # 6. Segment transitions
    typer.echo("")
    seg_gap = _prompt_int("Segment gap (ms)", 0, 0)
    crossfade = _prompt_int("Crossfade (ms)", 0, 0)
    if seg_gap != 0:
        values["segment_gap"] = seg_gap
    if crossfade != 0:
        values["crossfade"] = crossfade

    # ── Write toml ──────────────────────────────────────────────────────────
    config_path.write_text(_build_toml(values, engine))
    typer.echo(f"\nCreated voicecli.toml with {len(values)} setting(s).")

    # ── Offer PATH install ────────────────────────────────────────────────
    _offer_path_install()


def _list_voices_for_engine(engine_name: str) -> list[str]:
    """Get voice list without loading heavy models."""
    if engine_name in QWEN_ENGINES:
        from voicecli.engines.qwen import SPEAKERS

        return list(SPEAKERS)
    return ["default"]


def _offer_path_install() -> None:
    """Offer to create a wrapper script in ~/.local/bin so voicecli works globally."""
    import os
    import shutil

    bin_dir = Path.home() / ".local" / "bin"
    wrapper = bin_dir / "voicecli"
    project_dir = Path.cwd().resolve()

    # Skip if voicecli is already reachable outside uv (e.g. pipx, previous install)
    existing = shutil.which("voicecli")
    if existing and Path(existing).resolve() != (project_dir / ".venv" / "bin" / "voicecli"):
        return  # already installed globally via another method

    typer.echo("")
    install = typer.confirm(
        f"Install 'voicecli' command to {bin_dir}?\n"
        "  (creates a small wrapper so you can run voicecli from anywhere)",
        default=True,
    )
    if not install:
        return

    bin_dir.mkdir(parents=True, exist_ok=True)
    script = f'#!/usr/bin/env bash\ncd "{project_dir}" && exec uv run voicecli "$@"\n'

    if wrapper.exists():
        overwrite = typer.confirm(f"  {wrapper} already exists. Overwrite?", default=False)
        if not overwrite:
            typer.echo("  Skipped.")
            return

    wrapper.write_text(script)
    wrapper.chmod(0o755)

    # Check if ~/.local/bin is in PATH
    bin_dir_resolved = bin_dir.resolve()
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    in_path = any(Path(p).resolve() == bin_dir_resolved for p in path_dirs if p)

    typer.echo(f"  Installed wrapper to {wrapper}")
    if not in_path:
        typer.echo(
            f"  Note: {bin_dir} is not in your PATH. Add this to your shell rc:\n"
            f'    export PATH="$HOME/.local/bin:$PATH"'
        )


def _prompt_float(label: str, default: float, lo: float, hi: float) -> float:
    while True:
        raw = typer.prompt(f"{label} ({lo}-{hi})", default=str(default)).strip()
        try:
            val = float(raw)
            if lo <= val <= hi:
                return val
        except ValueError:
            pass
        typer.echo(f"  Must be a number between {lo} and {hi}.")


def _prompt_int(label: str, default: int, minimum: int) -> int:
    while True:
        raw = typer.prompt(label, default=str(default)).strip()
        try:
            val = int(raw)
            if val >= minimum:
                return val
        except ValueError:
            pass
        typer.echo(f"  Must be an integer >= {minimum}.")


def _build_toml(values: dict[str, object], engine: str) -> str:
    """Build voicecli.toml with set fields uncommented, others commented out."""
    is_qwen = engine.startswith("qwen")
    is_chatterbox = engine.startswith("chatterbox")

    def line(key: str, default: object, comment: str = "") -> str:
        suffix = f"  # {comment}" if comment else ""
        if key in values:
            v = values[key]
            val_str = f'"{v}"' if isinstance(v, str) else str(v)
            return f"{key} = {val_str}{suffix}"
        else:
            val_str = f'"{default}"' if isinstance(default, str) else str(default)
            return f"# {key} = {val_str}{suffix}"

    lines = ["[defaults]"]
    lines.append(line("engine", "qwen", "qwen | chatterbox | chatterbox-turbo | qwen-fast"))
    if engine != "chatterbox-turbo":
        lines.append(line("language", "English"))
    if is_qwen:
        lines.append(line("voice", "Vivian"))

    if is_qwen:
        lines.append("")
        lines.append("# ── Structured instruct parts (Qwen) ──")
        lines.append('# Auto-compose into instruct: "accent. personality. speed. emotion"')
        lines.append("# Write them in the target language.")
        for field, example in [
            ("accent", "Light southern French accent"),
            ("personality", "Calm, warm and articulate"),
            ("speed", "Measured pace with natural pauses"),
            ("emotion", "Warm and engaged"),
        ]:
            lines.append(line(field, example))

    if is_chatterbox:
        lines.append("")
        lines.append("# ── Chatterbox expressiveness ──")
        lines.append(line("exaggeration", 0.5, "0.25-2.0 (default 0.5)"))
        lines.append(line("cfg_weight", 0.5, "0.0-1.0 (default 0.5)"))

    lines.append("")
    lines.append("# ── Segment transitions ──")
    lines.append(line("segment_gap", 0, "ms silence between segments"))
    lines.append(line("crossfade", 0, "ms fade between segments"))
    lines.append("")
    return "\n".join(lines)


@app.command()
def doctor():
    """Check system readiness: Python, CUDA, models, directories, disk space."""
    import platform
    import shutil
    import sys

    from voicecli.models import MODEL_REGISTRY, cached_model_size_gb, hf_cache_dir, is_model_cached

    def ok(msg: str) -> None:
        typer.echo(typer.style("  \u2713 ", fg=typer.colors.GREEN, bold=True) + msg)

    def warn(msg: str) -> None:
        typer.echo(typer.style("  ! ", fg=typer.colors.YELLOW, bold=True) + msg)

    def fail(msg: str) -> None:
        typer.echo(typer.style("  \u2717 ", fg=typer.colors.RED, bold=True) + msg)

    typer.echo(typer.style("\nVoiceCLI Doctor", bold=True))
    typer.echo("=" * 40)

    # Python version
    try:
        v = sys.version_info
        label = f"Python {v.major}.{v.minor}.{v.micro}"
        if (v.major, v.minor) in ((3, 11), (3, 12)):
            ok(label)
        else:
            warn(f"{label} (recommended: 3.11-3.12)")
    except Exception:
        fail("Could not determine Python version")

    # uv installed
    try:
        if shutil.which("uv"):
            ok("uv installed")
        else:
            fail("uv not found (install: https://docs.astral.sh/uv/)")
    except Exception:
        fail("Could not check for uv")

    # CUDA availability
    try:
        import torch

        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            ok(f"CUDA available: {name} ({vram:.1f} GB VRAM)")
        else:
            fail("CUDA not available (GPU required for TTS)")
    except ImportError:
        fail("PyTorch not installed")
    except Exception as e:
        fail(f"CUDA check failed: {e}")

    # Model cache status
    typer.echo(typer.style("\nModel Cache", bold=True) + f"  ({hf_cache_dir()})")
    for repo_id, (display_name, est_size) in MODEL_REGISTRY.items():
        try:
            if is_model_cached(repo_id):
                actual = cached_model_size_gb(repo_id)
                size_str = f"{actual} GB" if actual is not None else "cached"
                ok(f"{display_name}: {size_str}")
            else:
                warn(f"{display_name}: not yet downloaded (~{est_size} GB)")
        except Exception as e:
            fail(f"{display_name}: could not check cache ({e})")

    # Directory structure
    typer.echo(typer.style("\nDirectories", bold=True))
    for d in ["TTS/voices_out", "TTS/samples", "TTS/texts_in", "STT/audio_in", "STT/texts_out"]:
        try:
            p = Path(d)
            if p.exists():
                ok(d)
            else:
                warn(f"{d} (will be created on first use)")
        except Exception:
            fail(f"{d}: check failed")

    # User config
    typer.echo(typer.style("\nConfig", bold=True))
    try:
        from voicecli.config import load_defaults

        cfg = load_defaults()
        if cfg:
            ok(f"voicecli.toml loaded ({len(cfg)} defaults: {', '.join(cfg)})")
        else:
            config_path = Path("voicecli.toml")
            if config_path.is_file():
                warn("voicecli.toml found but [defaults] section is empty")
            else:
                warn("No voicecli.toml (optional — set default language, engine, etc.)")
    except Exception as e:
        warn(f"Could not read voicecli.toml: {e}")

    # Active voice sample
    typer.echo(typer.style("\nVoice Sample", bold=True))
    try:
        from voicecli.samples import get_active

        active = get_active()
        if active:
            ok(f"Active sample: {active}")
        else:
            warn("No active sample set (needed for clone)")
    except Exception:
        warn("Could not check active sample")

    # Disk space
    typer.echo(typer.style("\nDisk Space", bold=True))
    try:
        usage = shutil.disk_usage(Path.home())
        free_gb = usage.free / (1024**3)
        if free_gb > 20:
            ok(f"{free_gb:.1f} GB free")
        elif free_gb > 5:
            warn(f"{free_gb:.1f} GB free (models need ~10 GB)")
        else:
            fail(f"{free_gb:.1f} GB free (insufficient for model downloads)")
    except Exception:
        warn("Could not check disk space")

    typer.echo(typer.style(f"\nPlatform: {platform.platform()}", dim=True))
    typer.echo()


@app.command()
def serve(
    engine: Annotated[
        Optional[str],
        typer.Option("--engine", "-e", help="Engine to preload on startup (e.g. qwen)"),
    ] = None,
    fast: Annotated[bool, typer.Option("--fast", help="Use smaller Qwen model")] = False,
) -> None:
    """Start the daemon to keep models loaded for fast generation.

    Run in the background so subsequent 'voicecli generate' calls skip the ~60s cold start.

    Example supervisord config:

    \b
    [program:voicecli_daemon]
    command=uv run --directory /path/to/voiceCLI voicecli serve --engine qwen
    autostart=true
    autorestart=true
    stdout_logfile=/var/log/voicecli_daemon.log
    """
    from voicecli.daemon import daemon_main

    daemon_main(preload=engine, fast=fast)


@app.command("stt-serve")
def stt_serve(
    model: Annotated[
        str,
        typer.Option("--model", "-m", help="Whisper model to load (default: large-v3-turbo)"),
    ] = "",
) -> None:
    """Start the STT daemon to keep faster-whisper loaded for fast dictation.

    Run in the background and trigger with 'voicecli dictate'.

    Example supervisord config:

    \\b
    [program:voicecli_stt]
    command=uv run --directory /path/to/voiceCLI voicecli stt-serve
    autostart=true
    autorestart=true
    stdout_logfile=/var/log/voicecli_stt.log
    """
    from voicecli.config import load_config
    from voicecli.stt_daemon import SttDaemon

    cfg = load_config()
    resolved_model = (
        model or (cfg.get("stt", {}).get("model", "") if cfg else "") or "large-v3-turbo"
    )
    SttDaemon(model=resolved_model).serve()


@app.command()
def emotions():
    """Show available emotion/expressiveness controls for each engine."""
    typer.echo(
        """Emotion & Expressiveness Controls
==================================

Qwen (structured instruct parts)
---------------------------------
Use structured parts to control the voice character:
  accent:      "Leger accent du sud provencal"  (pronunciation, origin)
  personality: "Calme, douce et flamboyante"    (character traits)
  speed:       "Rythme pose"                    (tempo/pace)
  emotion:     "Chaleureuse"                    (emotional state)

Parts auto-compose into instruct:
  "Leger accent du sud provencal. Calme, douce et flamboyante. Rythme pose. Chaleureuse"

Per-section override:
  <!-- emotion: "Passionnee" -->  (only emotion changes, rest inherited)

IMPORTANT: Write instruct parts in the TARGET LANGUAGE.
  French speech  -> accent: "Leger accent provencal"
  English speech -> accent: "Light southern French accent"
  Japanese speech -> accent in Japanese, etc.

Raw 'instruct' still works as bypass — overrides all parts.
Set in frontmatter, per-section directives, or voicecli.toml.

Qwen (raw instruct mode)
-------------------------
Use 'instruct' for full free-form control (in the target language):
  instruct: "Parle avec colere"          (French target)
  instruct: "Speak angrily"              (English target)
  instruct: "En chuchotant doucement"    (French target)

Chatterbox Turbo (paralinguistic tags — English only)
-----------------------------------------------------
Insert tags directly in your text (engine: chatterbox-turbo):
  [laugh]  [chuckle]  [cough]  [sigh]
  [gasp]   [groan]    [sniff]  [shush]
  [clear throat]

Chatterbox (numeric controls — both turbo & multilingual)
---------------------------------------------------------
Set in .md frontmatter or per-section via <!-- key: value -->:
  exaggeration: 0.25 - 2.0  (expressiveness, default 0.5)
  cfg_weight:   0.0  - 1.0  (speaker adherence, default 0.5)

Per-Section Directives
----------------------
Override any parameter per section using HTML comments:
  <!-- accent: "Accent parisien" -->           (Qwen)
  <!-- emotion: "Passionnee" -->               (Qwen)
  <!-- instruct: "Parle serieusement" -->      (Qwen, raw bypass)
  <!-- exaggeration: 0.8 -->                   (Chatterbox)
  <!-- language: Japanese -->                  (per-section language)
  <!-- voice: Ono_Anna -->                     (per-section voice, Qwen)
  <!-- segment_gap: 500 -->                    (silence before section, ms)
  <!-- crossfade: 100 -->                      (fade before section, ms)

Segment Transitions
-------------------
  gap=0, crossfade=0    direct concat (default)
  gap>0, crossfade=0    hard cut | silence | hard cut
  gap=0, crossfade>0    fade-out then fade-in
  gap>0, crossfade>0    fade-out | silence | fade-in

Set via frontmatter, per-section directives, CLI flags, or voicecli.toml.
"""
    )
