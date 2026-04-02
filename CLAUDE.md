@.claude/stack.yml
@.claude/dev-core.md

# VoiceCLI

Unified CLI for local voice generation with Qwen3-TTS, Chatterbox Multilingual, Chatterbox Turbo and Voxtral backends.

## TL;DR

- **Project:** VoiceCLI
- **Before work:** Use `/dev #N` as the single entry point — it determines tier (S / F-lite / F-full) and drives the full lifecycle
- **Decisions:** → see global patterns (@.claude/dev-core.md)
- **Never** commit without asking, push without request, or use `--force`/`--hard`/`--amend`
- **Always** use appropriate skill even without slash command

### Code Review

MUST read [code-review](docs/standards/code-review.md). Conventional Comments. Block only: security, correctness, standard violations.

### Coding Standards

| Context | Read |
|---------|------|
| API / Backend | [backend-patterns](docs/standards/backend-patterns.md) |
| Tests | [testing](docs/standards/testing.md) |

## Usage Reference

**For CLI commands, engine capabilities, markdown format, frontmatter fields, directives, config, and workflow** → see [`skills/voice/SKILL.md`](skills/voice/SKILL.md) (single source of truth).

Priority chain: **CLI flag > markdown frontmatter > voicecli.toml > hardcoded default**

## Code Pipeline (internal)

```
User runs: voicecli generate script.md -e chatterbox

  1. cli.py         — detects .md input, resolves engine from CLI flag / frontmatter
  2. markdown.py    — parses YAML frontmatter + body into TTSDocument
                      (extracts instruct, segments, tags, exaggeration, etc.)
  2b. api.py        — _apply_config_defaults(): backfills voicecli.toml structured parts
                      (accent, personality, speed, emotion) into doc/segments, recomposes instruct
  3. translate.py   — adapts TTSDocument for the target engine via ENGINE_CAPS matrix
                      (strips/converts tags, nulls unsupported fields)
  4. api.py         — extracts fields from translated doc into engine kwargs
  5. engine/*.py    — generates audio (chunking, model inference, WAV output)
  6. utils.py       — optional MP3 conversion
```

Each step is pure Python, no LLM involved. The translator is the key piece — it makes
one universal `.md` file work across all engines without manual adaptation.

### LLM skill (`skills/voice/SKILL.md`)

The `/voicecli` skill lives at `skills/voice/SKILL.md` (source of truth, part of the self-contained plugin).
`.claude/skills/voicecli/SKILL.md` and `roxabi-plugins/plugins/voice-cli/skills/voice/SKILL.md` are both symlinks to it.
Install directly: `claude plugin marketplace add Roxabi/voiceCLI && claude plugin install voice-cli`

The LLM does NOT translate documents — that is handled by `translate.py` in the code pipeline.
The skill just needs to know that unified format exists so it can write scripts using all features.

## Tech Stack

- Python 3.12, managed with `uv`
- CLI framework: Typer
- TTS engines: `qwen-tts` (Qwen3-TTS), `chatterbox-tts` (Chatterbox Multilingual + Turbo), `voxtral-tts` (Voxtral 4B int4)
- Audio: `soundfile`, `lameenc` (MP3 encoding)
- Recording: PulseAudio CLI (`parecord`/`paplay`) — works on WSL2 via WSLg
- Linting: `ruff` (line-length 100, target py312)
- GPU: PyTorch 2.7+ cu128 for RTX 5070 Ti (Blackwell sm_120)

## Project Layout

```
voicecli.example.toml — template config — copy to ~/.voicecli/voicecli.toml
TTS/
  texts_in/         — authored .md scripts (tracked in git)
~/.voicecli/
  voicecli.toml     — user config (global, all projects)
  voicecli.vocab    — personal vocabulary for STT (shared with Lyra)
  TTS/
    voices_out/     — generated WAV/MP3
    samples/        — voice samples for cloning
  STT/
    audio_in/       — recorded audio from dictate
    texts_out/      — transcription results
src/voicecli/
  cli.py            — Typer app: command definitions, .md detection, flag overrides
  api.py            — Public API: generate(), clone(), transcribe() + config resolution
  config.py         — TOML config loader (reads voicecli.toml)
  engine.py         — Abstract TTSEngine base class + engine registry
  translate.py      — Engine capability matrix (ENGINE_CAPS) + translate_for_engine()
  markdown.py       — YAML frontmatter parser + markdown-to-plaintext + directive parser
  utils.py          — Output path helper + concat_audio() + WAV→MP3 conversion
  samples.py        — Sample management + PulseAudio recording with chimes
  transcribe.py     — Faster Whisper file transcription
  listen.py         — Kyutai STT real-time mic transcription
  overlay.py        — Waveform overlay (GTK3 + gtk-layer-shell on Wayland, X11 fallback); stop.wav on close
  assets/           — UI sounds: start.wav (mic tap) + stop.wav (slowed tap); start_mic/stop_mic alternates
  engines/
    qwen.py              — Qwen3-TTS engine (CustomVoice for generate, Base for clone)
    chatterbox.py        — Chatterbox Multilingual engine (23 languages, segment-aware)
    chatterbox_turbo.py  — Chatterbox Turbo engine (English-only, paralinguistic tags)
    voxtral.py           — Voxtral 4B engine (int4, 20 voice presets, 9 languages)
```

## Key Patterns

- Engines are lazy-loaded (model loaded on first use, not import)
- Engine registry in `engine.py:_get_registry()` — add new engines there
- `QWEN_ENGINES = frozenset({"qwen", "qwen-fast"})` in `engine.py` — single source for Qwen engine names
- Daemon (`daemon.py`) keeps Qwen models in VRAM; `generate`/`clone` try daemon first for Qwen engines, fall back silently to standalone if socket absent or any error occurs
- Daemon socket: `~/.local/share/voicecli/daemon.sock` (AF_UNIX, newline-delimited JSON)
- `generate` and `clone` both accept raw text OR a `.md` file path (auto-detected)
- `clone` falls back to active sample when `--ref` is omitted
- Config backfill (`_apply_config_defaults`) runs after `parse_md_file()`, before `translate_for_engine()`
- Translation happens after config backfill but before field extraction in api.py
- All engines support segment-aware generation with per-segment parameter overrides
- Qwen clone uses `x_vector_only_mode=True` when no `--ref-text` is provided
- Qwen clone does NOT support `instruct` — only `generate` (CustomVoice) does
- Both Chatterbox engines split long text into sentence chunks (~250 chars) to avoid 40s cutoff
- Chatterbox Multilingual clone defaults to cfg_weight=0.0 to reduce accent bleed in cross-language cloning

## Conventions

- No over-engineering — this is a thin CLI, keep it flat and simple
- Imports of heavy libs (torch, qwen_tts, chatterbox, voxtral_tts) are deferred to function bodies
- Output WAVs/MP3s go to `~/.voicecli/TTS/voices_out/` by default
- Samples stored in `~/.voicecli/TTS/samples/`
- Transcription results saved to `~/.voicecli/STT/texts_out/` by default
- Dictate recordings saved to `~/.voicecli/STT/audio_in/`
- Scripts authored in `TTS/texts_in/` (project-local, tracked in git)
- Override conflicts in `[tool.uv] override-dependencies` in pyproject.toml
- Audio playback/recording uses PulseAudio CLI tools (paplay/parecord), not sounddevice

## STT / Dictate — Key Patterns

- **AHK shortcuts** (Windows): `Alt+Shift+Space` = toggle, `Alt+Shift+Tab` = next-mode, `Alt+Shift+Esc` = cancel
- **AHK script location**: `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\voicecli-dictate.ahk`
- **Wrapper scripts**: `~/.local/bin/voicecli-dictate`, `voicecli-next-mode`, `voicecli-cancel`
- **Auto-paste on WSL2**: daemon writes `%TEMP%\voicecli_paste_trigger` → AHK polls it every 150ms → sends `^v`
- **Auto-paste config**: `auto_paste = true` in `[stt]` section of `voicecli.toml` (requires daemon restart)
- **UI sounds**: start.wav played by `stt_daemon._play_ui_sound()` (zero-latency, before overlay spawns); stop.wav played by overlay on `_close()`
- **No chimes in stt_daemon**: `_chime()` removed — overlay handles all UI sounds
- **Overlay shortcuts are display-only**: Tab/Esc in overlay toolbar are informational; actual shortcuts go through AHK
- **CLI commands**: `voicecli dictate cancel` | `voicecli dictate next-mode` | `voicecli dictate status`

## Gotchas

### Daemons managed by supervisord (lyra-stack)

`voicecli serve` (TTS) and `voicecli stt-serve` (STT) are **not standalone processes** — they are managed by supervisord via `~/projects/lyra-stack`. Killing them with `kill` will not work: supervisord auto-restarts them within seconds.

To temporarily stop a daemon, use supervisorctl:
```bash
cd ~/projects/lyra-stack
make tts stop      # stop TTS daemon
make stt stop      # stop STT daemon
make tts start     # restart TTS daemon
make stt start     # restart STT daemon
make ps            # check status of all services
```

### VRAM contention on RTX 3080 (10 GB)

The TTS daemon (qwen-fast) uses ~7.4 GB VRAM and the STT daemon uses ~2.2 GB — together they fill the entire GPU. When both are running, `voicecli clone` (or any operation requiring additional VRAM allocations) will fail with CUDA OOM even though the model is already loaded.

**Fix**: stop the STT daemon via supervisord before running a clone operation, then restart it after:
```bash
make -C ~/projects/lyra-stack stt stop
voicecli clone "text" -e qwen-fast
make -C ~/projects/lyra-stack stt start
```
