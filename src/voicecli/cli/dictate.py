import sys
from typing import Annotated, Optional

import typer

dictate_app = typer.Typer(
    help="Dictation client for STT daemon",
    epilog=(
        "Quick setup:\n\n"
        "  1. Start daemon:  voicecli stt-serve\n\n"
        "  2. Bind a hotkey to: voicecli dictate\n\n"
        "     WSL2 (global):  Windows Settings > Custom Shortcuts > 'wsl voicecli dictate'\n\n"
        "     KDE:            System Settings > Custom Shortcuts > Command\n\n"
        "     GNOME:          Settings > Keyboard > Custom Shortcuts\n\n"
        "     X11 only:       voicecli dictate --listen  (pynput required)\n\n"
        "  Full guide: docs/dictation-setup.md"
    ),
)


def _run_dictate_setup() -> None:
    """Print a step-by-step setup guide tailored to the current environment."""
    import os
    import shutil
    from pathlib import Path

    username = Path.home().name

    # Detect environment
    is_wsl = "WSL_DISTRO_NAME" in os.environ or (
        Path("/proc/version").exists() and "microsoft" in Path("/proc/version").read_text().lower()
    )
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    is_kde = "KDE" in desktop.upper()
    is_gnome = "GNOME" in desktop.upper()

    typer.echo("VoiceCLI Dictate — Setup Guide")
    typer.echo("=" * 40)
    typer.echo("")
    typer.echo("Step 1: Start the STT daemon")
    typer.echo("  voicecli stt-serve")
    typer.echo("  (add to autostart — see Step 3)")
    typer.echo("")

    if is_wsl:
        typer.echo("Step 2: Bind a hotkey — Windows AutoHotkey (recommended)")
        typer.echo("  Save this as dictate.ahk in your Windows Startup folder:")
        typer.echo(r"  (%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup)")
        typer.echo("")
        typer.echo("    ^Space:: {")
        typer.echo(f'        RunWait "wsl /home/{username}/.local/bin/voicecli-dictate", , "Hide"')
        typer.echo("    }")
        typer.echo("    !+Tab:: {")
        typer.echo(
            f'        RunWait "wsl /home/{username}/.local/bin/voicecli-next-mode", , "Hide"'
        )
        typer.echo("    }")
        typer.echo("")
        typer.echo("  Or: Windows Settings > Bluetooth & Devices > Keyboard > Custom Shortcuts")
        typer.echo(f"  Command: wsl /home/{username}/.local/bin/voicecli-dictate")
        typer.echo("")
        typer.echo("Step 3: Clipboard — ensure wl-clipboard or xclip is installed:")
        typer.echo("  sudo apt install wl-clipboard")
        typer.echo("")
        typer.echo("Step 4: Overlay — ensure GTK layer-shell is installed:")
        typer.echo("  sudo apt install gir1.2-gtklayershell-0.1")
    elif is_kde:
        typer.echo("Step 2: KDE System Settings > Shortcuts > Custom Shortcuts")
        typer.echo("  Edit > New > Global Shortcut > Command/URL")
        typer.echo("  Trigger: your key combo (e.g. Alt+Shift+Space)")
        typer.echo("  Action: voicecli dictate")
        typer.echo("")
        typer.echo("  For mode cycling (Alt+Shift+Tab):")
        typer.echo("  Action: voicecli dictate next-mode")
        typer.echo("")
        typer.echo("Step 3: Auto-start daemon")
        typer.echo("  Add to KDE Autostart (System Settings > Autostart):")
        typer.echo("  voicecli stt-serve")
    elif is_gnome:
        typer.echo("Step 2: GNOME Settings > Keyboard > Keyboard Shortcuts > Custom Shortcuts")
        typer.echo("  Name: VoiceCLI Dictate")
        typer.echo("  Command: voicecli dictate")
        typer.echo("  Shortcut: Alt+Shift+Space")
        typer.echo("")
        typer.echo("  Name: VoiceCLI Next Mode")
        typer.echo("  Command: voicecli dictate next-mode")
        typer.echo("  Shortcut: Alt+Shift+Tab")
        typer.echo("")
        typer.echo("Step 3: Auto-start daemon")
        typer.echo("  Create ~/.config/autostart/voicecli-stt.desktop:")
        typer.echo("")
        typer.echo("    [Desktop Entry]")
        typer.echo("    Type=Application")
        typer.echo("    Name=VoiceCLI STT Daemon")
        typer.echo("    Exec=voicecli stt-serve")
        typer.echo("    Hidden=false")
        typer.echo("    NoDisplay=false")
        typer.echo("    X-GNOME-Autostart-enabled=true")
    else:
        typer.echo("Step 2: Bind your DE's keyboard shortcut to:")
        typer.echo("  voicecli dictate          (toggle recording)")
        typer.echo("  voicecli dictate next-mode  (cycle modes)")
        typer.echo("")
        typer.echo("Step 3: Auto-start daemon at login via your DE's autostart or:")
        typer.echo("  Add to ~/.profile: voicecli stt-serve &")

    typer.echo("")
    typer.echo("Dependency check:")

    # Clipboard
    if shutil.which("wl-copy") or shutil.which("xclip") or shutil.which("clip.exe"):
        typer.echo("  clipboard OK")
    else:
        typer.echo("  clipboard: install wl-clipboard")

    # Notifications
    if shutil.which("notify-send"):
        typer.echo("  notifications OK")
    else:
        typer.echo("  notifications: install libnotify-bin")

    # Overlay (GTK3 + gtk-layer-shell)
    try:
        import gi  # type: ignore[import-untyped]

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk  # type: ignore[import-untyped]  # noqa: F401

        typer.echo("  overlay OK")
    except (ValueError, ImportError):
        typer.echo("  overlay: sudo apt install libgtk-3-0")


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
    mode: Annotated[
        Optional[str],
        typer.Option("--mode", help="STT mode to use for this recording (e.g. french, code)"),
    ] = None,
    setup: Annotated[
        bool,
        typer.Option("--setup", help="Print step-by-step setup guide for this environment"),
    ] = False,
) -> None:
    """Toggle dictation recording or start the hotkey listener."""
    if ctx.invoked_subcommand is not None:
        return

    if setup:
        _run_dictate_setup()
        return

    if listen:
        from voicecli.core.config import load_stt_config
        from voicecli.ui.dictate_client import hotkey_loop

        stt_cfg = load_stt_config()
        hotkey_loop(stt_cfg["hotkey"], paste=paste)
        return

    from voicecli.ui.dictate_client import auto_paste, notify, send_toggle

    resp = send_toggle(mode=mode)

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
    from voicecli.ui.dictate_client import send_status

    resp = send_status()
    if resp.get("status") == "error":
        print(resp.get("message", "unknown error"), file=sys.stderr)
        raise typer.Exit(code=1)
    print(resp.get("state", "unknown"))


@dictate_app.command("test-overlay")
def dictate_test_overlay() -> None:
    """Show the waveform overlay for 5 seconds (for testing position/visibility)."""
    import os
    import sys as _sys
    import subprocess

    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    # Patch overlay to stay open regardless of daemon state
    env["VOICECLI_OVERLAY_TEST"] = "1"
    typer.echo("Showing overlay for 5 seconds — look at the top of your screen...")
    subprocess.run([_sys.executable, "-m", "voicecli.ui.overlay", "--test"], env=env)


@dictate_app.command("next-mode")
def dictate_next_mode() -> None:
    """Cycle to the next STT mode (becomes the new default)."""
    from voicecli.ui.dictate_client import notify, send_next_mode

    resp = send_next_mode()
    if resp.get("status") == "error":
        print(resp.get("message", "unknown error"), file=sys.stderr)
        raise typer.Exit(code=1)
    mode = resp.get("mode", "")
    desc = resp.get("description", mode)
    print(f"mode: {mode}")
    notify(f"Mode: {desc}", timeout=2000)


@dictate_app.command("cancel")
def dictate_cancel() -> None:
    """Cancel the current STT recording without transcribing."""
    from voicecli.ui.dictate_client import send_cancel

    resp = send_cancel()
    if resp.get("status") == "error":
        print(resp.get("message", "unknown error"), file=sys.stderr)
        raise typer.Exit(code=1)


@dictate_app.command("modes")
def dictate_modes() -> None:
    """List all available STT modes with descriptions."""
    from voicecli.core.config import load_config
    from voicecli.core.dictate_modes import load_modes

    cfg = load_config()
    modes = load_modes(cfg)
    if not modes:
        typer.echo("No modes available.")
        return
    col_w = max(len(n) for n in modes) + 2
    for name, m in sorted(modes.items()):
        desc = m.get("description", "")
        parts = []
        if m.get("language"):
            parts.append(f"language={m['language']}")
        if m.get("task") and m["task"] != "transcribe":
            parts.append(f"task={m['task']}")
        if parts:
            desc = f"{desc}  [{', '.join(parts)}]" if desc else ", ".join(parts)
        typer.echo(f"  {name:<{col_w}}{desc}")


@dictate_app.command("history")
def dictate_history(
    json_output: Annotated[
        bool, typer.Option("--json", help="Print raw JSONL instead of a table")
    ] = False,
    copy: Annotated[
        Optional[int],
        typer.Option("--copy", help="Copy entry N (1-based from most-recent) to clipboard"),
    ] = None,
) -> None:
    """Show the last 20 dictation history entries."""
    import json as _json

    from voicecli.ui.clipboard import write_clipboard
    from voicecli.core.history import HISTORY_PATH

    if not HISTORY_PATH.exists():
        typer.echo("No history yet.")
        return

    lines = [ln for ln in HISTORY_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()]
    # Most recent last in file — show last 20, most recent at bottom
    entries = []
    for line in lines:
        try:
            entries.append(_json.loads(line))
        except _json.JSONDecodeError:
            pass

    recent = entries[-20:]

    if copy is not None:
        # --copy 1 = most recent
        idx = len(recent) - copy
        if idx < 0 or idx >= len(recent):
            typer.echo(f"Entry {copy} out of range (1–{len(recent)}).", err=True)
            raise typer.Exit(1)
        write_clipboard(recent[idx]["text"])
        typer.echo(f"Copied entry {copy} to clipboard.")
        return

    if json_output:
        for e in recent:
            typer.echo(_json.dumps(e, ensure_ascii=False))
        return

    # Table output
    typer.echo(f"{'#':>3}  {'time':>8}  {'lang':>4}  {'mode':<14}  text")
    typer.echo("-" * 72)
    for i, e in enumerate(recent, 1):
        ts = e.get("ts", "")[-8:] if e.get("ts") else ""  # HH:MM:SS
        lang = (e.get("language") or "")[:4]
        mode_str = (e.get("mode") or "")[:14]
        text_preview = e.get("text", "")[:40]
        if len(e.get("text", "")) > 40:
            text_preview += "..."
        typer.echo(f"{i:>3}  {ts:>8}  {lang:>4}  {mode_str:<14}  {text_preview}")


# ── NATS-based dictation ────────────────────────────────────────────────────────


@dictate_app.command("nats-host")
def dictate_nats_host() -> None:
    """Print the resolved NATS ``host:port`` (env > ``[nats]`` toml).

    Used by the dictate wrapper to do a fast TCP probe before invoking the
    blocking ``dictate nats`` toggle. Prints nothing and exits 0 when no URL
    is configured (the wrapper then skips the pre-flight check).
    """
    import os
    from urllib.parse import urlparse

    from voicecli.core.config import apply_nats_env_from_config

    apply_nats_env_from_config()
    url = os.environ.get("NATS_URL", "").strip()
    if not url:
        return
    parsed = urlparse(url)
    if not parsed.hostname:
        return
    port = parsed.port or 4222
    typer.echo(f"{parsed.hostname}:{port}")


@dictate_app.command("nats")
def dictate_nats(
    paste: Annotated[
        bool, typer.Option("--paste", help="Auto-paste transcribed text into focused window")
    ] = False,
    model: Annotated[
        str, typer.Option("--model", "-m", help="STT model for transcription")
    ] = "large-v3-turbo",
    language: Annotated[
        Optional[str], typer.Option("--lang", help="Force language code (e.g., en, fr)")
    ] = None,
    mode: Annotated[
        Optional[str],
        typer.Option("--mode", help="STT mode (e.g. french, default, code) — selects prompt+task"),
    ] = None,
    timeout: Annotated[
        float, typer.Option("--timeout", "-t", help="NATS request timeout in seconds")
    ] = 60.0,
) -> None:
    """Toggle NATS-based dictation recording.

    First call: starts recording in the background.
    Second call: stops recording, transcribes via NATS, copies to clipboard.

    Reads ``NATS_URL`` (and optional ``NATS_NKEY_SEED_PATH``) from the
    environment, falling back to the ``[nats]`` table in ``voicecli.toml``
    (keys: ``url``, ``nkey_seed_path``).
    """
    import asyncio

    from voicecli.ui.clipboard import write_clipboard
    from voicecli.core.config import (
        apply_nats_env_from_config,
        load_config,
        load_vocab,
        vocab_to_prompt,
    )
    from voicecli.ui.nats_mic_recorder import is_recording, start_recording, stop_recording
    from voicecli.adapters.nats.transcribe_client import transcribe_via_nats
    from voicecli.ui.dictate_client import notify
    from voicecli.core.dictate_modes import get_mode
    from voicecli.ui.sounds import play_ui_sound

    apply_nats_env_from_config()

    # Resolve mode (prompt + task + optional language) like the socket daemon does.
    mode_prompt: Optional[str] = None
    mode_task: Optional[str] = None
    if mode is not None:
        try:
            mode_cfg = get_mode(mode, load_config())
        except ValueError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)
        mode_prompt = mode_cfg.get("prompt")
        mode_task = mode_cfg.get("task")
        if language is None and "language" in mode_cfg:
            language = mode_cfg["language"]

    # Vocab is prepended to the prompt — same shape as dictation._stop_and_transcribe.
    try:
        vocab_fragment = vocab_to_prompt(load_vocab())
    except Exception:
        vocab_fragment = None
    if vocab_fragment:
        initial_prompt = f"{vocab_fragment} {mode_prompt}" if mode_prompt else vocab_fragment
    else:
        initial_prompt = mode_prompt

    if is_recording():
        # Stop and transcribe
        play_ui_sound("stop_mic.wav")
        notify("Transcribing...", timeout=0)
        wav_bytes = stop_recording()

        if not wav_bytes:
            typer.echo("No audio recorded", err=True)
            notify("No audio recorded", timeout=3000)
            raise typer.Exit(1)

        result = asyncio.run(
            transcribe_via_nats(
                wav_bytes,
                model=model,
                language=language,
                initial_prompt=initial_prompt,
                task=mode_task,
                timeout=timeout,
            )
        )

        if "error" in result:
            typer.echo(f"Error: {result['error']}", err=True)
            notify(f"Error: {result['error']}", timeout=5000)
            raise typer.Exit(1)

        text = result["text"]
        language_result = result.get("language", "")

        print(text)

        try:
            write_clipboard(text)
        except Exception as e:
            typer.echo(f"Warning: clipboard write failed: {e}", err=True)

        preview = text[:50] + ("..." if len(text) > 50 else "")
        lang_tag = f"[{language_result}] " if language_result else ""
        notify(f"{lang_tag}{preview}", timeout=3000)

        if paste:
            from voicecli.ui.clipboard import auto_paste

            auto_paste()
    else:
        # Start recording
        resp = start_recording(model=model, language=language)
        if "error" in resp:
            typer.echo(f"Error: {resp['error']}", err=True)
            raise typer.Exit(1)

        play_ui_sound("start_mic.wav")
        notify("Recording...", timeout=0)
        print("recording")
