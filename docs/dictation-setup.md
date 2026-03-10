---
title: Dictation Setup
description: How to set up voicecli dictate with a keyboard shortcut for hands-free dictation
---

## Overview

`voicecli dictate` is a thin socket client for the STT daemon (`voicecli stt-serve`). Each
invocation connects to the daemon's Unix socket, sends a `toggle` action, and returns
immediately — there is no model loading on the client side. The daemon handles recording,
transcription, and clipboard write; the client reads the result from the response and shows
a desktop notification.

Typical flow:

1. First press: daemon starts recording. Notification: "Recording..."
2. Second press: daemon stops recording, transcribes, writes text to clipboard. Notification shows the transcribed text.
3. Ctrl+V in any app to paste.

## Prerequisites

The STT daemon must be running before `voicecli dictate` can do anything:

```bash
voicecli stt-serve
```

The daemon loads the faster-whisper model eagerly at startup (default: `large-v3-turbo`).
Keep it running in the background — a supervisord config is shown in `voicecli stt-serve --help`.

To verify the daemon is ready:

```bash
voicecli dictate status
```

Expected output: `idle`

## Recommended: Windows Keyboard Shortcut

This is the primary path for WSL2 users. A Windows keyboard shortcut can trigger
`wsl voicecli dictate` from any focused application — including native Windows apps,
browsers, and Office tools. No X11 focus is required.

### Windows Settings (built-in)

1. Open **Settings > Bluetooth & devices > Keyboard > Advanced keyboard settings**.
2. Under **Custom keyboard shortcuts**, add a new shortcut.
3. Set the command to:

```
wsl voicecli dictate
```

4. Assign your preferred key combination (e.g. `Alt+Shift+Space`).

When triggered, the `wsl` launcher starts the WSL2 process, runs `voicecli dictate`,
and exits. The clipboard is shared between WSL2 and Windows — text written by the
daemon via `xclip`/`wl-copy` is immediately available for Ctrl+V in any Windows app.
Notifications appear in the WSLg notification area in the Windows system tray.

### AutoHotkey Alternative

If you prefer AutoHotkey (v2), save this as `dictate.ahk` and run it at login:

```ahk
; AutoHotkey v2 — trigger voicecli dictate from anywhere
!+Space:: {  ; Alt+Shift+Space
    RunWait "wsl voicecli dictate", , "Hide"
}
```

For AutoHotkey v1:

```ahk
; AutoHotkey v1 — trigger voicecli dictate from anywhere
!+Space::
    RunWait, wsl voicecli dictate,, Hide
return
```

Adjust the hotkey combination (`!` = Alt, `+` = Shift, `^` = Ctrl, `#` = Win) to
your preference.

## Alternative: Built-in Hotkey Listener

`voicecli dictate --listen` starts a persistent pynput listener that watches for the
configured hotkey and calls `voicecli dictate` on each press.

**Limitation:** pynput captures keys only when an X11 or WSLg window has focus. If you
switch to a native Windows app, the listener will not receive key events. Use the Windows
keyboard shortcut approach above for global coverage.

### Install pynput

pynput is an optional dependency. Install it into the voicecli environment:

```bash
uv pip install pynput
```

### Start the listener

```bash
voicecli dictate --listen
```

Output:

```
Listening for alt+space... (Ctrl+C to stop)
```

Press Ctrl+C to stop the listener.

### Configure the hotkey

In `voicecli.toml`, add an `[stt]` section:

```toml
[stt]
hotkey = "alt+space"
```

The default is `alt+space`. The hotkey string uses pynput's format: modifier names
(`alt`, `ctrl`, `shift`, `cmd`) joined with `+`, followed by the key name.

Examples:

```toml
hotkey = "ctrl+shift+space"
hotkey = "alt+f9"
hotkey = "cmd+shift+d"
```

### Combine with auto-paste

```bash
voicecli dictate --listen --paste
```

With `--paste`, after each successful transcription the text is typed into the focused
X11/WSLg window via `xdotool`. See [Auto-paste](#auto-paste) for details.

## Linux Desktop Shortcuts

### KDE Plasma

1. Open **System Settings > Shortcuts > Custom Shortcuts**.
2. Click **Edit > New > Global Shortcut > Command/URL**.
3. Name it "Dictate".
4. Under **Trigger**, assign your key combination.
5. Under **Action**, set the command to:

```
voicecli dictate
```

If `voicecli` is not on the KDE session PATH, use the full path:

```
/home/<user>/.local/bin/voicecli dictate
```

Or via `uv run`:

```
bash -c 'cd /path/to/voiceCLI && uv run voicecli dictate'
```

### GNOME

1. Open **Settings > Keyboard > Keyboard Shortcuts > Custom Shortcuts**.
2. Click **+** to add a new shortcut.
3. Set the name to "Dictate" and the command to `voicecli dictate`.
4. Click **Set Shortcut** and press your key combination.

## Auto-paste

The `--paste` flag triggers `xdotool type` to type the transcribed text directly into
the focused window after each successful transcription.

```bash
voicecli dictate --paste
```

**Limitation:** `xdotool` only works in X11/XWayland windows. For native Windows apps,
it has no effect. The text is always written to the clipboard regardless of `--paste`,
so Ctrl+V always works as a fallback.

If `xdotool` is not installed, `--paste` silently does nothing. Install it with:

```bash
sudo apt install xdotool
```

## Status Check

Check the current daemon state without toggling:

```bash
voicecli dictate status
```

Possible outputs:

| Output | Meaning |
|--------|---------|
| `idle` | Daemon is running, not recording |
| `recording` | Currently capturing audio |
| `transcribing` | Audio captured, model running |
| `queued` | Toggle received during transcription; will record next |

If the daemon is not running, the command exits with code 1 and prints:

```
STT daemon not running
```

## Notifications

When `notify-send` is available, `voicecli dictate` sends desktop notifications:

| Event | Notification |
|-------|-------------|
| Recording started | "Recording..." (persistent until next event) |
| Transcription complete | First 50 characters of transcribed text |
| Queued | "Queued..." |
| Daemon not running | "STT daemon not running" |

Notifications use a stable replace-ID (`voicecli-dictate`) so each new notification
replaces the previous one rather than stacking.

On WSL2 with WSLg, notifications appear in the Windows system tray notification area.

Install `notify-send` if not present:

```bash
sudo apt install libnotify-bin
```

If `notify-send` is not installed, notifications are silently skipped — the command
still functions normally.

## Troubleshooting

**"STT daemon not running"**

Start the daemon:

```bash
voicecli stt-serve
```

Wait for the "Ready" message before triggering dictate. The model load takes 10–30
seconds depending on your GPU.

**No notifications appear**

Check that `notify-send` is installed:

```bash
which notify-send
```

If missing: `sudo apt install libnotify-bin`. On WSL2, also verify that WSLg is active
(an X server process should be running — check Task Manager for `wslg.exe`).

**Hotkey not captured with `--listen`**

pynput requires an active X11/WSLg session and focus on an X11 window. If you are
working in a native Windows app, the hotkey will not fire. Switch to the Windows
keyboard shortcut approach described in [Recommended: Windows Keyboard Shortcut](#recommended-windows-keyboard-shortcut).

**Transcription is slow after restart**

The daemon loads the model at startup. If `voicecli stt-serve` was just started, wait
for `[voicecli stt] Ready on ...` before dictating. Subsequent toggles have sub-second
latency.

**Toggle triggers twice**

The built-in listener applies a 300ms debounce. If you are triggering via an external
script or hotkey manager that sends multiple key events, add a short delay between
invocations on the Windows/AHK side.

## Configuration Reference

Add an `[stt]` table to `voicecli.toml` to configure STT behavior:

```toml
[stt]
model  = "large-v3-turbo"   # Whisper model (overridden by --model flag)
hotkey = "alt+space"        # Hotkey for --listen mode
```

| Key | Default | Description |
|-----|---------|-------------|
| `model` | `large-v3-turbo` | faster-whisper model loaded by `stt-serve` at startup |
| `hotkey` | `alt+space` | Hotkey string for `voicecli dictate --listen` |

Priority chain: `CLI flag > voicecli.toml > hardcoded default`

See [Configuration](./configuration) for config file discovery rules.
