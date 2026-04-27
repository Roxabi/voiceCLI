from pathlib import Path

import typer


def doctor():
    """Check system readiness: Python, CUDA, models, directories, disk space."""
    import platform
    import shutil
    import sys

    from voicecli.models import MODEL_REGISTRY, cached_model_size_gb, hf_cache_dir, is_model_cached

    def ok(msg: str) -> None:
        typer.echo(typer.style("  ✓ ", fg=typer.colors.GREEN, bold=True) + msg)

    def warn(msg: str) -> None:
        typer.echo(typer.style("  ! ", fg=typer.colors.YELLOW, bold=True) + msg)

    def fail(msg: str) -> None:
        typer.echo(typer.style("  ✗ ", fg=typer.colors.RED, bold=True) + msg)

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
    from voicecli.config import VOICECLI_DIR

    for rel in ["TTS/voices_out", "TTS/samples", "TTS/texts_in", "STT/audio_in", "STT/texts_out"]:
        try:
            p = VOICECLI_DIR / rel
            if p.exists():
                ok(str(p))
            else:
                warn(f"{p} (will be created on first use)")
        except Exception:
            fail(f"{rel}: check failed")

    # User config
    typer.echo(typer.style("\nConfig", bold=True))
    try:
        from voicecli.config import load_defaults

        cfg = load_defaults()
        if cfg:
            ok(f"voicecli.toml loaded ({len(cfg)} defaults: {', '.join(cfg)})")
        else:
            config_path = VOICECLI_DIR / "voicecli.toml"
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
