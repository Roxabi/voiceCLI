@.claude/stack.yml

# VoiceCLI

Unified CLI for local voice gen — Qwen3-TTS, Chatterbox Multilingual, Chatterbox Turbo, Voxtral backends.

Python 3.12 via `uv` · Typer CLI · PyTorch 2.7+ cu128 · ruff (L≤100, py312) · audio: `soundfile`, `lameenc`, PulseAudio CLI (WSL2 via WSLg).

## TL;DR

- **Project:** VoiceCLI
- **Before work:** `/dev #N` = single entry — picks tier (S / F-lite / F-full) + drives lifecycle
- **Never:** `--force` / `--hard` / `--amend`
- **Always:** use matching skill even w/o slash cmd

### Code Review

MUST read [code-review](docs/standards/code-review.md). Conventional Comments. Block only: security, correctness, standards violations.

### Coding Standards

| Context | Read |
|---|---|
| API / Backend | [backend-patterns](docs/standards/backend-patterns.md) |
| Tests | [testing](docs/standards/testing.md) |

## Usage Reference

**CLI, engine caps, markdown format, frontmatter, directives, config, workflow** → [`plugins/voice-cli/skills/voice/SKILL.md`](plugins/voice-cli/skills/voice/SKILL.md) (single source of truth).

Priority: **CLI flag > frontmatter > voicecli.toml > default**

Pipeline (cli → markdown → api → translate → engine → utils) + LLM skill install → [`docs/architecture/pipeline.md`](docs/architecture/pipeline.md).

## Project Layout

```
voicecli.example.toml — template — copy → ~/.roxabi/voicecli/voicecli.toml
TTS/
  texts_in/         — authored .md scripts (git-tracked)
~/.roxabi/voicecli/
  voicecli.toml     — user config (global, all projects)
  voicecli.vocab    — personal STT vocabulary (shared w/ Lyra)
  TTS/
    voices_out/     — generated WAV/MP3
    samples/        — voice clone samples
  STT/
    audio_in/       — recorded audio from dictate
    texts_out/      — transcription results
src/voicecli/
  cli.py            — Typer app: commands, .md detection, flag overrides
  api.py            — Public API: generate(), clone(), transcribe() + config resolution
  config.py         — TOML loader (reads voicecli.toml)
  engine.py         — Abstract TTSEngine + registry
  translate.py      — ENGINE_CAPS matrix + translate_for_engine()
  markdown.py       — YAML frontmatter + md→text + directive parser
  utils.py          — Output path + concat_audio() + WAV→MP3
  samples.py        — Sample mgmt + PulseAudio record w/ chimes
  transcribe.py     — Faster Whisper file transcription
  listen.py         — Kyutai STT real-time mic transcription
  overlay.py        — Waveform overlay (GTK3 + gtk-layer-shell on Wayland, X11 fallback); stop.wav on close
  nats_mic_recorder.py  — Background mic recorder for NATS dictation (toggle, state ∈ ~/.local/share/voicecli/)
  transcribe_client.py — One-shot NATS STT client; inbox_prefix=_inbox.voice-client (ACL ADR-051)
  assets/           — UI sounds: start.wav (mic tap) + stop.wav (slowed); start_mic/stop_mic alts
  engines/
    qwen.py              — Qwen3-TTS (CustomVoice generate, Base clone)
    chatterbox.py        — Chatterbox Multilingual (23 languages, segment-aware)
    chatterbox_turbo.py  — Chatterbox Turbo (English-only, paralinguistic tags)
    voxtral.py           — Voxtral 4B (int4, 20 presets, 9 languages)
```

## Key Patterns (invariants)

- Engines lazy — model loads on 1st use, ¬on import
- Registry ∈ `engine.py:_get_registry()` — add engines here
- `QWEN_ENGINES = frozenset({"qwen", "qwen-fast"})` ∈ `engine.py` — single source for Qwen names
- Daemon (`daemon.py`) keeps Qwen models ∈ VRAM; `generate`/`clone` try daemon first for Qwen, silently fall back to standalone iff socket absent ∨ any error
- Daemon socket: `~/.local/share/voicecli/daemon.sock` (AF_UNIX, newline-delimited JSON)
- `generate` ∧ `clone` both accept raw text ∨ `.md` path (auto-detected)
- `clone` falls back to active sample iff `--ref` omitted
- Config backfill (`_apply_config_defaults`) runs post `parse_md_file()`, pre `translate_for_engine()`
- All engines: segment-aware generation w/ per-segment param overrides

Engine-specific nuances (Qwen `x_vector_only_mode`, Chatterbox chunking @ 250 chars, `cfg_weight=0.0` for Multilingual clone cross-lang) → documented in engine modules.

## Conventions

- ¬over-engineering — thin CLI, flat + simple
- Heavy imports (torch, qwen_tts, chatterbox, voxtral_tts) deferred to function bodies
- Output WAV/MP3 → `~/.roxabi/voicecli/TTS/voices_out/` default
- Samples → `~/.roxabi/voicecli/TTS/samples/`
- Transcription → `~/.roxabi/voicecli/STT/texts_out/` default
- Dictate recordings → `~/.roxabi/voicecli/STT/audio_in/`
- Scripts authored ∈ `TTS/texts_in/` (project-local, git-tracked)
- Override conflicts ∈ `[tool.uv] override-dependencies` ∈ pyproject.toml
- Audio playback/record: PulseAudio CLI (paplay/parecord), ¬sounddevice

## STT / Dictate

→ [`docs/STT-SETUP.md`](docs/STT-SETUP.md) — AHK shortcuts, WSL2 auto-paste, overlay sounds, CLI commands.

Two dictation modes:
- `voicecli dictate` — socket daemon + overlay (same-host, Unix socket)
- `voicecli dictate nats` — NATS toggle (cross-host; M₂ mic → M₁ STT satellite); requires `NATS_URL` + `NATS_NKEY_SEED_PATH`

## Gotchas

### Daemon management by host

**M₁ (roxabituwer, voice-worker) — Quadlet:**
`voicecli-tts` and `voicecli-stt` run as Podman containers under systemd. `kill` on the process won't stick — systemd restarts within `RestartSec=10`.

```bash
systemctl --user stop voicecli-tts      # stop TTS satellite
systemctl --user stop voicecli-stt      # stop STT satellite
systemctl --user start voicecli-tts     # restart TTS
systemctl --user start voicecli-stt     # restart STT
systemctl --user status voicecli-{tts,stt}  # status
journalctl --user -u voicecli-tts -f    # logs
```

**M₂ (roxabitower, dev) — native socket daemon:**
`voicecli serve` (TTS) ∧ `voicecli stt-serve` (STT) run as native processes. Stop with `pkill` or Ctrl-C in the terminal.

### VRAM contention on RTX 3080 (10 GB)

TTS daemon (qwen-fast) ~7.4 GB + STT daemon ~2.2 GB → fills whole GPU. Both running → `voicecli clone` (∨ any op needing extra VRAM alloc) fails CUDA OOM even though model already loaded.

**Fix:** stop STT satellite before clone, restart after:
```bash
# M₁ (Quadlet):
systemctl --user stop voicecli-stt
voicecli clone "text" -e qwen-fast
systemctl --user start voicecli-stt

# M₂ (native):
pkill -f 'voicecli stt-serve'
voicecli clone "text" -e qwen-fast
voicecli stt-serve &
```

### NATS satellite vs socket daemon — pick one mode per host

`voicecli nats-serve` ∧ `voicecli serve` / `stt-serve` compete for same GPU. VRAM-sequencing guard refuses to start satellite iff live socket daemon detected (exit 78). Three coexistence modes:

| Mode | When | How |
|---|---|---|
| **socket-only** | Hub + voicecli on same host (low-latency local dev) | Run `voicecli serve` / `stt-serve`, ¬start NATS satellite |
| **nats-only** (default) | Prod — hub on different host | Stop socket daemon, run `voicecli nats-serve tts` / `stt` |
| **allow-coexist** | Large-VRAM dev boxes (RTX 5070 Ti 16GB, …) only | `--allow-coexist` ∨ `VOICECLI_ALLOW_COEXIST=1` |

¬allow-coexist on RTX 3080 (10GB) prod — OOMs under concurrent synthesis. Full guard + exit codes: [`docs/NATS-SERVE.md#vram-sequencing`](docs/NATS-SERVE.md#vram-sequencing).

### NATS client — synthesize from any host

From a non-satellite host (M₂, laptop, …) → `voicecli generate --via-nats "text"` routes synthesis to the hub TTS satellite over NATS, decodes the reply, writes the WAV locally. ¬loads model on caller. Mirror of `voicecli dictate nats` (STT side).

| Flag / env | Effect |
|---|---|
| `--via-nats` ∨ `VOICECLI_VIA_NATS=1` | Bypass socket-daemon/standalone; request/reply on `lyra.voice.tts.request` |
| `--timeout <s>` | NATS request timeout (default 60s) |
| `.md` input | Frontmatter + segments flattened client-side → single `text` field sent to satellite (V1, no per-segment multi-language) |

Requires `NATS_URL` + `NATS_NKEY_SEED_PATH` env (same as STT client).

### Container deployment (Quadlet)

Production hosts can run voiceCLI as a Podman container managed by systemd via Quadlet. Unit files in `deploy/quadlet/` define TTS and STT NATS satellites with GPU passthrough.

| File | Purpose |
|---|---|
| `deploy/Dockerfile.tts` | TTS image — CUDA 12.4 + uv + extras |
| `deploy/Dockerfile.stt` | STT image — CUDA 12.4 + uv + extras |
| `deploy/entrypoint.sh` | Mode selector (`tts` \| `stt`) → `nats-serve` |
| `deploy/quadlet/voicecli-tts.container` | TTS satellite systemd unit |
| `deploy/quadlet/voicecli-stt.container` | STT satellite systemd unit |
| `deploy/quadlet.toml` | Manifest (components, host_roles, secrets) |
| `deploy/install.sh` | Idempotent installer (secrets + Quadlets + daemon-reload) |

Full Quadlet setup: [`docs/QUADLET-DEPLOYMENT.md`](docs/QUADLET-DEPLOYMENT.md).
