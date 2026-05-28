# STT / Dictate — Setup & Key Patterns

## AHK shortcuts (Windows)

- `Alt+Shift+Space` — toggle dictate on/off
- `Alt+Shift+Tab` — next mode
- `Alt+Shift+Esc` — cancel

**AHK script location:** `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\voicecli-dictate.ahk`

## Wrapper scripts

- `~/.local/bin/voicecli-dictate`
- `~/.local/bin/voicecli-next-mode`
- `~/.local/bin/voicecli-cancel`

## Auto-paste on WSL2

Flow: daemon writes `%TEMP%\voicecli_paste_trigger` → AHK polls every 150ms → sends `^v`

Enable: `auto_paste = true` in `[stt]` section of `voicecli.toml` (requires daemon restart)

## UI sounds

- `start.wav` — played by `sounds.play_ui_sound()` (zero-latency, before overlay spawns)
- `stop.wav` — played by overlay on `_close()`
- `_chime()` removed from transcribe_daemon — overlay handles all UI sounds
- Overlay shortcuts (Tab/Esc in toolbar) are display-only — actual shortcuts go through AHK

## CLI commands

```bash
voicecli dictate cancel
voicecli dictate next-mode
voicecli dictate status
```

## NATS dictation (cross-host, `dictate nats`)

Toggle pattern — same shortcut starts and stops recording. Audio is sent to a remote STT satellite.

### Wrapper script

```bash
# ~/.local/bin/voicecli-dictate-nats
#!/bin/bash
export NATS_URL="nats://192.168.1.16:4222"
export NATS_NKEY_SEED_PATH="$HOME/.roxabi/voicecli/nkeys/voice-client.seed"
exec /home/mickael/.local/bin/voicecli dictate nats
```

### COSMIC (Pop!_OS Wayland) shortcut

System Settings → Keyboard → Custom Shortcuts → `+`
- Command: `/home/mickael/.local/bin/voicecli-dictate-nats`
- Shortcut: `Ctrl+Space` (or preferred combo)

Alternatively via gsettings (GNOME):
```bash
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings \
  "['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/voicecli-dictate/']"
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/voicecli-dictate/ \
  command "/home/mickael/.local/bin/voicecli-dictate-nats"
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/voicecli-dictate/ \
  binding "<Super><Alt>space"
```

### Prerequisites

- `NATS_URL` pointing to M₁ (port 4222 must be reachable on the LAN)
- `voice-client.seed` at `~/.roxabi/voicecli/nkeys/voice-client.seed` (NKey identity with `lyra.voice.stt.request` publish + `_inbox.voice-client.>` subscribe)
- `voicecli nats-serve stt` running on M₁ (via `voicecli-stt.service` Quadlet)

### UI sounds

- `start_mic.wav` — on recording start
- `stop_mic.wav` — on recording stop (before NATS transcription request)
