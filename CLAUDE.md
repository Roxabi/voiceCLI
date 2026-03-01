# VoiceMe

Unified CLI for local voice generation with Qwen3-TTS, Chatterbox Multilingual and Chatterbox Turbo backends.

## Global Workflow — What Handles What

### Code pipeline (deterministic, at runtime)

```
User runs: voiceme generate script.md -e chatterbox

  1. cli.py         — detects .md input, resolves engine from CLI flag / frontmatter
  2. markdown.py    — parses YAML frontmatter + body into TTSDocument
                      (extracts instruct, segments, tags, exaggeration, etc.)
  3. translate.py   — adapts TTSDocument for the target engine via ENGINE_CAPS matrix
                      (strips/converts tags, nulls unsupported fields)
  4. cli.py         — extracts fields from translated doc into engine kwargs
  5. engine/*.py    — generates audio (chunking, model inference, WAV output)
  6. utils.py       — optional MP3 conversion
```

Each step is pure Python, no LLM involved. The translator is the key new piece — it makes
one universal `.md` file work across all three engines without manual adaptation.

### LLM skill (`.claude/skills/voiceme/SKILL.md`)

The `/voiceme` skill is invoked when the user asks Claude Code to generate speech, clone
voices, or manage samples. The LLM handles:

- **Intent parsing** — understanding natural language requests ("make me a French speech with laughter")
- **Command selection** — picking the right `uv run voiceme` command and flags
- **Script authoring** — writing `.md` files with appropriate frontmatter when the user wants a script
- **Engine guidance** — knowing which features each engine supports (from the Engine Notes)

The LLM does NOT translate documents — that is handled by `translate.py` in the code pipeline.
The skill just needs to know that unified format exists so it can write scripts using all features.

## Tech Stack

- Python 3.12, managed with `uv`
- CLI framework: Typer
- TTS engines: `qwen-tts` (Qwen3-TTS), `chatterbox-tts` (Chatterbox Multilingual + Turbo)
- Audio: `soundfile`, `lameenc` (MP3 encoding)
- Recording: PulseAudio CLI (`parecord`/`paplay`) — works on WSL2 via WSLg
- Linting: `ruff` (line-length 100, target py312)
- GPU: PyTorch 2.7+ cu128 for RTX 5070 Ti (Blackwell sm_120)

## Project Layout

```
voiceme.toml        — user config file for default settings (optional, see below)
TTS/
  texts_in/         — authored .md scripts (tracked in git)
  voices_out/       — generated WAV/MP3 (gitignored)
  samples/          — voice samples for cloning (gitignored)
STT/
  audio_in/         — audio files to transcribe (gitignored)
  texts_out/        — transcription results (gitignored)
src/voiceme/
  cli.py            — Typer app: command definitions, .md detection, flag overrides
  config.py         — TOML config loader (reads voiceme.toml)
  engine.py         — Abstract TTSEngine base class + engine registry
  translate.py      — Engine capability matrix (ENGINE_CAPS) + translate_for_engine()
  markdown.py       — YAML frontmatter parser + markdown-to-plaintext + directive parser
  utils.py          — Output path helper + concat_audio() + WAV→MP3 conversion
  samples.py        — Sample management + PulseAudio recording with chimes
  transcribe.py     — Faster Whisper file transcription
  listen.py         — Kyutai STT real-time mic transcription
  engines/
    qwen.py              — Qwen3-TTS engine (CustomVoice for generate, Base for clone)
    chatterbox.py        — Chatterbox Multilingual engine (23 languages, segment-aware)
    chatterbox_turbo.py  — Chatterbox Turbo engine (English-only, paralinguistic tags)
```

## User Config (`voiceme.toml`)

Optional TOML file at project root. Sets default values so you don't pass flags every time.

```toml
[defaults]
language = "French"
engine = "chatterbox"
exaggeration = 0.7
cfg_weight = 0.3
segment_gap = 200       # ms silence between segments
crossfade = 50          # ms fade between segments
```

Priority: **CLI flag > markdown frontmatter > voiceme.toml > hardcoded default**

## Engine Capability Matrix

Defined in `translate.py:ENGINE_CAPS` — drives all translation decisions:

| Capability     | Qwen            | Chatterbox Multilingual | Chatterbox Turbo |
|----------------|-----------------|-------------------------|------------------|
| `instruct`     | yes             | no (nulled)             | no (nulled)      |
| `segments`     | yes             | yes                     | yes              |
| `tags`         | `to_instruct`   | `strip`                 | `native`         |
| `exaggeration` | no (nulled)     | yes (per-segment)       | yes (per-segment)|
| `cfg_weight`   | no (nulled)     | yes (per-segment)       | yes (per-segment)|
| `language`     | yes (per-segment)| yes (per-segment)      | no (nulled)      |
| `voice`        | yes (per-segment)| no (nulled)            | no (nulled)      |

Tag handling modes:
- `native` — keep `[laugh]` etc. in text as-is (engine processes them)
- `strip` — remove all `[tag]` markers (engine can't use them)
- `to_instruct` — split text at tags, create new segments with mapped instruct (e.g. `[laugh]` → `instruct: "Laughing"`)

## All CLI Commands

```bash
# Speech generation (built-in voices)
voiceme generate "text"                    # Qwen default voice (Ryan)
voiceme generate "text" -e chatterbox      # Chatterbox Multilingual engine
voiceme generate "text" -e chatterbox-turbo # Chatterbox Turbo (English, emotion tags)
voiceme generate script.md                 # from markdown with frontmatter
voiceme generate script.md --mp3           # also save as MP3
voiceme generate script.md --segment-gap 300  # 300ms silence between segments
voiceme generate script.md --crossfade 50     # 50ms fade between segments

# Voice cloning
voiceme clone "text" --ref voice.wav       # clone from reference audio
voiceme clone "text"                       # uses active sample (no --ref needed)
voiceme clone script.md --mp3              # from markdown + MP3 output

# Sample management
voiceme samples list                       # list all .wav in TTS/samples/
voiceme samples add file.wav               # import a WAV file
voiceme samples record name -d 30          # record 30s from mic (PulseAudio)
voiceme samples use name.wav               # set as active sample for clone
voiceme samples active                     # show current active sample
voiceme samples remove name.wav            # delete a sample

# Speech-to-text
voiceme transcribe audio.wav               # transcribe audio file
voiceme transcribe audio.wav --json        # JSON with language + timestamps
voiceme transcribe audio.wav -m large-v3   # choose model
voiceme transcribe audio.wav -l fr         # force language
voiceme listen                             # live mic → text (Kyutai)
voiceme listen -m 2.6b                     # English-only model

# Utilities
voiceme mp3 TTS/voices_out/file.wav       # convert WAV to MP3 (192kbps default)
voiceme mp3 TTS/voices_out/file.wav -b 320 # convert at 320kbps
voiceme voices                             # list Qwen voices
voiceme voices -e chatterbox               # list Chatterbox voices
voiceme engines                            # list available engines
voiceme emotions                           # emotion/expressiveness cheat sheet
```

## Unified Markdown Format

Write one `.md` file using ALL features — the translator adapts it per engine:

```markdown
---
language: French
instruct: "Speak warmly"
exaggeration: 0.7
cfg_weight: 0.3
segment_gap: 200
crossfade: 50
---

Welcome everyone. [laugh] This is going to be fun!

<!-- instruct: "Speak seriously" -->
<!-- segment_gap: 500 -->
Now let me tell you something important. [sigh] It has been a long road.

<!-- language: Japanese -->
<!-- voice: Ono_Anna -->
<!-- crossfade: 0 -->
<!-- segment_gap: 300 -->
A section in Japanese with a different voice.
```

### All frontmatter fields (all optional)

```yaml
---
language: French          # any language name (Qwen + Chatterbox Multilingual)
voice: Ryan               # built-in voice name (Qwen only)
engine: qwen              # qwen | chatterbox | chatterbox-turbo
instruct: "Speak angrily" # free-form emotion/tone instruction (Qwen only)
exaggeration: 0.75        # expressiveness 0.25-2.0, default 0.5 (Chatterbox only)
cfg_weight: 0.3           # speaker adherence 0.0-1.0, default 0.5 (Chatterbox only)
segment_gap: 200          # ms silence between segments, default 0
crossfade: 50             # ms fade between segments, default 0
---
```

### In-body directives

All frontmatter fields can also be set per-section using HTML comments:

- `<!-- instruct: "..." -->` — per-section emotion (Qwen, ignored by Chatterbox)
- `<!-- exaggeration: 0.8 -->` — per-section expressiveness (Chatterbox)
- `<!-- cfg_weight: 0.3 -->` — per-section speaker adherence (Chatterbox)
- `<!-- language: Japanese -->` — per-section language
- `<!-- voice: Serena -->` — per-section voice (Qwen only)
- `<!-- segment_gap: 500 -->` — silence before this section (ms)
- `<!-- crossfade: 100 -->` — fade before this section (ms)
- `[laugh]` `[chuckle]` `[sigh]` etc. — paralinguistic tags (native on Turbo, converted to instruct on Qwen, stripped on Multilingual)

Directives accumulate before a text block and apply to the text that follows.
Each section inherits frontmatter defaults, overridden by its inline directives.

### Segment transitions

| gap | crossfade | Result |
|-----|-----------|--------|
| 0   | 0         | Direct concat (default) |
| >0  | 0         | Hard cut, silence, hard cut |
| 0   | >0        | Fade-out then fade-in (no silence) |
| >0  | >0        | Fade-out, silence, fade-in |

### Translation example

Given the universal script above, the translator produces:

**Qwen** (`tags: to_instruct`, `segments: True`):
- Segment 1: "Welcome everyone." — instruct: "Speak warmly"
- Segment 2: "This is going to be fun!" — instruct: "Laughing"
- Segment 3: "Now let me tell you something important." — instruct: "Speak seriously"
- Segment 4: "It has been a long road." — instruct: "Sighing"
- exaggeration/cfg_weight: nulled

**Chatterbox Multilingual** (`tags: strip`, `segments: True`):
- Segment 1: "Welcome everyone. This is going to be fun!" — exaggeration: 0.7, language: French
- Segment 2: "Now let me tell you something important. It has been a long road." — segment_gap: 500
- instruct: nulled per-segment

**Chatterbox Turbo** (`tags: native`, `segments: True`):
- Segment 1: "Welcome everyone. [laugh] This is going to be fun!" — exaggeration: 0.7
- Segment 2: "Now let me tell you something important. [sigh] It has been a long road." — segment_gap: 500
- instruct: nulled per-segment, language: nulled

## Key Patterns

- Engines are lazy-loaded (model loaded on first use, not import)
- Engine registry in `engine.py:_get_registry()` — add new engines there
- `generate` and `clone` both accept raw text OR a `.md` file path (auto-detected)
- `clone` falls back to active sample when `--ref` is omitted
- Priority chain: CLI flag > markdown frontmatter > voiceme.toml > hardcoded default
- Translation happens after engine resolution but before field extraction in cli.py
- All engines support segment-aware generation with per-segment parameter overrides
- Qwen clone uses `x_vector_only_mode=True` when no `--ref-text` is provided
- Qwen clone does NOT support `instruct` — only `generate` (CustomVoice) does
- Both Chatterbox engines split long text into sentence chunks (~250 chars) to avoid 40s cutoff
- Chatterbox Multilingual clone defaults to cfg_weight=0.0 to reduce accent bleed in cross-language cloning
- Recommended Chatterbox settings: passionate speech = exaggeration 0.7-0.8, cfg_weight 0.3

## Conventions

- No over-engineering — this is a thin CLI, keep it flat and simple
- Imports of heavy libs (torch, qwen_tts, chatterbox) are deferred to function bodies
- Output WAVs/MP3s go to `TTS/voices_out/` dir by default
- Samples stored in `TTS/samples/` dir
- Transcription results saved to `STT/texts_out/` by default
- Scripts authored in `TTS/texts_in/`
- Override conflicts in `[tool.uv] override-dependencies` in pyproject.toml
- Audio playback/recording uses PulseAudio CLI tools (paplay/parecord), not sounddevice
