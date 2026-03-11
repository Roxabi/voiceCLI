---
title: Configuration
description: voicecli.toml reference and configuration resolution
---

## Config File

voiceCLI uses `voicecli.toml` (gitignored). Copy from `voicecli.example.toml` and customize.

### Discovery

`load_defaults()` walks up from CWD to `$HOME` looking for `voicecli.toml`. A config at `~/projects/voicecli.toml` is shared across all projects under `~/projects/`. If not found, a warning is printed to stderr and built-in defaults are used.

### Priority Chain

```
CLI flag / API kwarg  >  markdown frontmatter  >  voicecli.toml  >  hardcoded default
```

See [Backend Patterns — Configuration Pattern](./standards/backend-patterns#14-configuration-pattern) for implementation details.

### Reference

```toml
[defaults]
language = "French"
engine = "qwen"
accent = "Leger accent du sud provencal"
personality = "Voix calme, douce et flamboyante"
exaggeration = 0.7
cfg_weight = 0.3
segment_gap = 200       # ms silence between segments
crossfade = 50          # ms fade between segments
# plain = false         # strip [tags] and ignore <!-- directives -->
# chunked = false       # always output separate chunk files
# chunk_size = 500      # target chunk size in chars (~15 chars/sec)
```

### Structured Instruct Composition

`accent`, `personality`, `speed`, `emotion` auto-compose into `instruct`:

```
"accent. personality. speed. emotion"
```

Only non-empty parts are joined. Raw `instruct` bypasses composition entirely.

See [Ubiquitous Language — Instruct vs Structured Parts](./architecture/ubiquitous-language#instruct-vs-structured-parts) for the distinction.

## STT Config (`[stt]`)

```toml
[stt]
model        = "large-v3-turbo"   # Whisper model (overridden by --model flag)
hotkey       = "alt+space"        # Hotkey for --listen mode
auto_paste   = true               # WSL2: write AHK trigger file after transcription
default_mode = "default"          # Starting mode on daemon launch
```

| Key | Default | Description |
|-----|---------|-------------|
| `model` | `large-v3-turbo` | faster-whisper model loaded by the STT daemon |
| `hotkey` | `alt+space` | Hotkey string for `voicecli dictate --listen` |
| `auto_paste` | `false` | Write AHK paste trigger after transcription (WSL2) |
| `default_mode` | `"default"` | Initial STT mode; cycle with `Alt+Shift+Tab` |

Requires daemon restart after changes. See [dictation-setup.md](./dictation-setup.md) for the full WSL2 setup.
