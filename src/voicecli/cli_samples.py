from pathlib import Path
from typing import Annotated

import typer

samples_app = typer.Typer(help="Manage voice samples")


@samples_app.command("list")
def samples_list():
    """List all samples in the TTS/samples/ directory."""
    from voicecli.core.samples import list_samples

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
    from voicecli.core.samples import add_sample

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
    from voicecli.core.samples import record_sample

    try:
        dest = record_sample(name, duration=duration)
    except (RuntimeError, OSError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Recorded {dest}")


@samples_app.command("use")
def samples_use(
    name: Annotated[str, typer.Argument(help="Sample filename to set as active")],
):
    """Set a sample as the active reference for voice cloning."""
    from voicecli.core.samples import set_active

    try:
        set_active(name)
        typer.echo(f"Active sample set to: {name}")
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@samples_app.command("active")
def samples_active():
    """Show the currently active sample."""
    from voicecli.core.samples import get_active

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
    from voicecli.core.samples import remove_sample

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
    from voicecli.core.samples import from_url, set_active

    try:
        dest = from_url(url, name, start=start, duration=duration)
        typer.echo(f"Added {dest}")
        if use:
            wav_name = dest.name
            set_active(wav_name)
            typer.echo(f"Active sample set to: {wav_name}")
    except ValueError as e:
        typer.echo(f"Invalid argument: {e}", err=True)
        raise typer.Exit(1)
    except RuntimeError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
