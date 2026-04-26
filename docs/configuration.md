---
title: Configuration
description: voicecli.toml reference and configuration resolution
---

## Install Profiles

ML libraries (torch, torchaudio) are optional extras — choose the profile that
matches your use case. `uv sync` with no extras installs only the CLI core.

| Profile | Command | What is installed |
|---------|---------|-------------------|
| **TTS dev** | `uv sync --extra tts` | Qwen3-TTS, faster-qwen3-tts, Chatterbox, torch, torchaudio |
| **STT dev** | `uv sync --extra stt` | Faster Whisper, torch, torchaudio |
| **Mock E2E / NATS satellite** | `uv sync --extra nats` | roxabi-nats, nats-py, nkeys, nvidia-ml-py (no torch) |
| **Full** | `uv sync --extra all` | Everything above combined (`tts + stt + nats`) |
| **Voxtral** | `uv sync --extra voxtral` | voxtral-tts (int4), scipy |
| **Hotkey** | `uv sync --extra hotkey` | pynput |
| **Overlay** | `uv sync --extra overlay` | PyGObject, pycairo |

**Docker image:** the `ghcr.io/roxabi/ml-base` base image ships torch and
torchaudio prebuilt for CUDA 12.8. The Dockerfile installs voicecli with
`--no-install-package torch torchaudio` so the pre-built wheels are used
instead of pulling from PyPI. Editable installs outside Docker resolve torch
via the `pytorch-cu128` index already declared in `pyproject.toml`.

## Config File

voiceCLI uses `voicecli.toml` (gitignored). Copy from `voicecli.example.toml` and customize.

### Discovery

`load_defaults()` checks `~/.voicecli/voicecli.toml` first, then walks up from CWD to `$HOME` as fallback. Place your config at `~/.voicecli/voicecli.toml` for global access regardless of which project you run from. If not found, a warning is printed to stderr and built-in defaults are used.

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
