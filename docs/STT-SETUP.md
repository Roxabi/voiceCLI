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

- `start.wav` — played by `stt_daemon._play_ui_sound()` (zero-latency, before overlay spawns)
- `stop.wav` — played by overlay on `_close()`
- `_chime()` removed from stt_daemon — overlay handles all UI sounds
- Overlay shortcuts (Tab/Esc in toolbar) are display-only — actual shortcuts go through AHK

## CLI commands

```bash
voicecli dictate cancel
voicecli dictate next-mode
voicecli dictate status
```
