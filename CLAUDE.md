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
voicecli.example.toml → ~/.roxabi/voicecli/voicecli.toml
TTS/texts_in/           — authored .md scripts (git-tracked)
~/.roxabi/voicecli/
  voicecli.toml         — user config
  voicecli.vocab        — personal STT vocabulary (shared w/ Lyra)
  TTS/{voices_out,samples}/  — generated audio / clone samples
  STT/{audio_in,texts_out}/  — recordings / transcriptions
src/voicecli/            — ports/adapters architecture, détail → `ls src/voicecli/`
  ports/                — abstract interfaces (STT/TTS/synthesis)
  adapters/              — port implementations: daemon/local synthesis, NATS transport (adapters/nats/)
  engines/               — TTS/STT backends (qwen, chatterbox, chatterbox_turbo, voxtral, mock)
  api/                   — public API surface (generate/clone/transcribe/translate/markdown)
  cli/                   — Typer app (main, dictate, doctor, nats, samples)
  core/                  — config, paths, models, samples, STT modes, history
  runtime/               — daemon, dictation, listen, model registry, wire protocol
  ui/                    — overlay, sounds, clipboard, NATS mic recorder, Telegram
  obs/                   — observability (otel wiring)
  assets/                — bundled sound files
```

## Key Patterns (invariants)

- Engines lazy — model loads on 1st use, ¬on import
- Registry ∈ `engine.py:_get_registry()` — add engines here
- `QWEN_ENGINES = frozenset({"qwen", "qwen-fast"})` ∈ `engine.py`
- Daemon keeps Qwen models ∈ VRAM; `generate`/`clone` try daemon first, fall back to standalone iff socket absent ∨ any error
- Daemon socket: `~/.local/share/voicecli/daemon.sock` (AF_UNIX, newline-delimited JSON)
- `generate` ∧ `clone` accept raw text ∨ `.md` path (auto-detected)
- `clone` falls back to active sample iff `--ref` omitted
- Config backfill runs post `parse_md_file()`, pre `translate_for_engine()`
- All engines: segment-aware generation w/ per-segment param overrides

## Conventions

- ¬over-engineering — thin CLI, flat + simple
- Heavy imports deferred to function bodies
- Output: `~/.roxabi/voicecli/TTS/voices_out/` (WAV/MP3), `TTS/samples/` (clones), `STT/texts_out/` (transcriptions), `STT/audio_in/` (dictate)
- Scripts authored ∈ `TTS/texts_in/` (project-local, git-tracked)
- Override conflicts ∈ `[tool.uv] override-dependencies` ∈ pyproject.toml
- Audio playback/record: PulseAudio CLI (paplay/parecord), ¬sounddevice

## STT / Dictate

→ [`docs/STT-SETUP.md`](docs/STT-SETUP.md) — modes: `voicecli dictate` (socket, same-host) and `voicecli dictate nats` (cross-host via NATS)

## Gotchas

| Topic | Pointer |
|---|---|
| Daemon management (M₁ Quadlet / M₂ native) | [`docs/QUADLET-DEPLOYMENT.md`](docs/QUADLET-DEPLOYMENT.md) |
| VRAM contention (RTX 3080 10 GB) | [`docs/VRAM-CONTENTION.md`](docs/VRAM-CONTENTION.md) |
| NATS satellite vs socket daemon | [`docs/NATS-SERVE.md#vram-sequencing`](docs/NATS-SERVE.md#vram-sequencing) |
| NATS client (`--via-nats`) | [`docs/NATS-SERVE.md#client-side--synthesize-from-any-host`](docs/NATS-SERVE.md#client-side--synthesize-from-any-host) |
| Container deployment (Quadlet) | [`docs/QUADLET-DEPLOYMENT.md`](docs/QUADLET-DEPLOYMENT.md) |
