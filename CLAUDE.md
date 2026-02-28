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
src/voiceme/
  cli.py          — Typer app: command definitions, .md detection, flag overrides
  engine.py       — Abstract TTSEngine base class + engine registry
  translate.py    — Engine capability matrix (ENGINE_CAPS) + translate_for_engine()
  markdown.py     — YAML frontmatter parser + markdown-to-plaintext + segment splitter
  utils.py        — Output path helper + WAV→MP3 conversion
  samples.py      — Sample management + PulseAudio recording with chimes
  transcribe.py   — Faster Whisper file transcription
  listen.py       — Kyutai STT real-time mic transcription
  engines/
    qwen.py            — Qwen3-TTS engine (CustomVoice for generate, Base for clone)
    chatterbox.py      — Chatterbox Multilingual engine (23 languages, sentence chunking)
    chatterbox_turbo.py — Chatterbox Turbo engine (English-only, paralinguistic tags)
```

## Engine Capability Matrix

Defined in `translate.py:ENGINE_CAPS` — drives all translation decisions:

| Capability     | Qwen            | Chatterbox Multilingual | Chatterbox Turbo |
|----------------|-----------------|-------------------------|------------------|
| `instruct`     | yes             | no (nulled)             | no (nulled)      |
| `segments`     | yes             | no (collapsed)          | no (collapsed)   |
| `tags`         | `to_instruct`   | `strip`                 | `native`         |
| `exaggeration` | no (nulled)     | yes                     | yes              |
| `cfg_weight`   | no (nulled)     | yes                     | yes              |
| `language`     | yes             | yes                     | no (nulled)      |
| `voice`        | yes             | no (nulled)             | no (nulled)      |

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

# Voice cloning
voiceme clone "text" --ref voice.wav       # clone from reference audio
voiceme clone "text"                       # uses active sample (no --ref needed)
voiceme clone script.md --mp3              # from markdown + MP3 output

# Sample management
voiceme samples list                       # list all .wav in samples/
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
voiceme mp3 output/file.wav               # convert WAV to MP3 (192kbps default)
voiceme mp3 output/file.wav -b 320        # convert at 320kbps
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
---

Welcome everyone. [laugh] This is going to be fun!

<!-- instruct: Speak seriously -->
Now let me tell you something important. [sigh] It has been a long road.
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
---
```

### In-body features

- `<!-- instruct: ... -->` — per-section emotion (Qwen segments, ignored by Chatterbox)
- `[laugh]` `[chuckle]` `[sigh]` etc. — paralinguistic tags (native on Turbo, converted to instruct on Qwen, stripped on Multilingual)

### Translation example

Given the universal script above, the translator produces:

**Qwen** (`tags: to_instruct`, `segments: True`):
- Segment 1: "Welcome everyone." — instruct: "Speak warmly"
- Segment 2: "This is going to be fun!" — instruct: "Laughing"
- Segment 3: "Now let me tell you something important." — instruct: "Speak seriously"
- Segment 4: "It has been a long road." — instruct: "Sighing"
- exaggeration/cfg_weight: nulled

**Chatterbox Multilingual** (`tags: strip`, `segments: False`):
- Flat text: "Welcome everyone. This is going to be fun! Now let me tell you something important. It has been a long road."
- instruct: nulled, exaggeration: 0.7, language: French

**Chatterbox Turbo** (`tags: native`, `segments: False`):
- Flat text: "Welcome everyone. [laugh] This is going to be fun! Now let me tell you something important. [sigh] It has been a long road."
- instruct: nulled, exaggeration: 0.7, language: nulled

## Key Patterns

- Engines are lazy-loaded (model loaded on first use, not import)
- Engine registry in `engine.py:_get_registry()` — add new engines there
- `generate` and `clone` both accept raw text OR a `.md` file path (auto-detected)
- `clone` falls back to active sample when `--ref` is omitted
- CLI flags always override frontmatter values
- Translation happens after engine resolution but before field extraction in cli.py
- Qwen clone uses `x_vector_only_mode=True` when no `--ref-text` is provided
- Qwen clone does NOT support `instruct` — only `generate` (CustomVoice) does
- Both Chatterbox engines split long text into sentence chunks (~250 chars) to avoid 40s cutoff
- Chatterbox Multilingual clone defaults to cfg_weight=0.0 to reduce accent bleed in cross-language cloning
- Recommended Chatterbox settings: passionate speech = exaggeration 0.7-0.8, cfg_weight 0.3

## Conventions

- No over-engineering — this is a thin CLI, keep it flat and simple
- Imports of heavy libs (torch, qwen_tts, chatterbox) are deferred to function bodies
- Output WAVs/MP3s go to `output/` dir by default
- Samples stored in `samples/` dir
- Override conflicts in `[tool.uv] override-dependencies` in pyproject.toml
- Audio playback/recording uses PulseAudio CLI tools (paplay/parecord), not sounddevice
