@.claude/stack.yml

# VoiceCLI

Unified CLI for local voice generation with Qwen3-TTS, Chatterbox Multilingual and Chatterbox Turbo backends.

## Global Workflow — What Handles What

### Code pipeline (deterministic, at runtime)

```
User runs: voicecli generate script.md -e chatterbox

  1. cli.py         — detects .md input, resolves engine from CLI flag / frontmatter
  2. markdown.py    — parses YAML frontmatter + body into TTSDocument
                      (extracts instruct, segments, tags, exaggeration, etc.)
  2b. cli.py        — _apply_config_defaults(): backfills voicecli.toml structured parts
                      (accent, personality, speed, emotion) into doc/segments, recomposes instruct
  3. translate.py   — adapts TTSDocument for the target engine via ENGINE_CAPS matrix
                      (strips/converts tags, nulls unsupported fields)
  4. cli.py         — extracts fields from translated doc into engine kwargs
  5. engine/*.py    — generates audio (chunking, model inference, WAV output)
  6. utils.py       — optional MP3 conversion
```

Each step is pure Python, no LLM involved. The translator is the key new piece — it makes
one universal `.md` file work across all three engines without manual adaptation.

### LLM skill (`roxabi-plugins/voice-me`)

The `/voicecli` skill is provided by the `voice-me` plugin (installed from `roxabi-plugins`).
Locally, `.claude/skills/voicecli/SKILL.md` is a symlink to the plugin (gitignored).

The LLM handles:

- **Intent parsing** — understanding natural language requests ("make me a French speech with laughter")
- **Command selection** — picking the right `uv run voicecli` command and flags
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
voicecli.example.toml — template config — copy to voicecli.toml (gitignored)
TTS/
  texts_in/         — authored .md scripts (tracked in git)
  voices_out/       — generated WAV/MP3 (gitignored)
  samples/          — voice samples for cloning (gitignored)
STT/
  audio_in/         — audio files to transcribe (gitignored)
  texts_out/        — transcription results (gitignored)
src/voicecli/
  cli.py            — Typer app: command definitions, .md detection, flag overrides
  config.py         — TOML config loader (reads voicecli.toml)
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

## User Config (`voicecli.toml`)

Optional TOML file (gitignored). Copy from `voicecli.example.toml` and customize.

**Discovery**: voicecli searches for `voicecli.toml` by walking up from the CWD to `$HOME`. This means a config at `~/projects/voicecli.toml` is shared across all projects under `~/projects/`. If no file is found, a warning is printed to stderr and built-in defaults are used.

```toml
[defaults]
language = "French"
engine = "qwen"
accent = "Léger accent du sud provençal"
personality = "Voix calme, douce et flamboyante"
exaggeration = 0.7
cfg_weight = 0.3
segment_gap = 200       # ms silence between segments
crossfade = 50          # ms fade between segments
# plain = false         # strip [tags] and ignore <!-- directives -->
# chunked = false       # always output separate chunk files
# chunk_size = 500      # target chunk size in chars (~15 chars/sec)
```

Structured instruct parts (`accent`, `personality`, `speed`, `emotion`) auto-compose into a single
`instruct` string: `"accent. personality. speed. emotion"`. Raw `instruct` bypasses composition.

**Segment propagation**: When a `.md` file is parsed, toml structured parts (accent, personality,
speed, emotion) are backfilled into segments where frontmatter didn't set them, then instruct is
recomposed. This means a `.md` file with no frontmatter still gets instruct from voicecli.toml.
Segments with a raw `instruct` bypass (instruct set but no structured parts) are left untouched.

Priority: **CLI flag > markdown frontmatter > voicecli.toml > hardcoded default**

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
- `to_instruct` — split text at tags, create new segments with mapped instruct (e.g. `[laugh]` → `instruct: "Laughing"`). Base instruct is preserved in both pre-tag and post-tag segments (e.g. `"Provençal. Calme, En riant, puis retrouve son calme"`).

## All CLI Commands

```bash
# Daemon (keep Qwen model warm — eliminates ~60s cold start)
voicecli serve                              # start daemon (lazy-loads on first request)
voicecli serve --engine qwen               # preload Qwen model at startup
voicecli serve --engine qwen --fast        # preload smaller 0.6B model

# Speech generation (built-in voices)
voicecli generate "text"                    # Qwen default voice; uses daemon if running
voicecli generate "text" -e chatterbox      # Chatterbox Multilingual engine
voicecli generate "text" -e chatterbox-turbo # Chatterbox Turbo (English, emotion tags)
voicecli generate script.md                 # from markdown with frontmatter
voicecli generate article.txt               # from plain text file
voicecli generate script.md --mp3           # also save as MP3
voicecli generate script.md --segment-gap 300  # 300ms silence between segments
voicecli generate script.md --crossfade 50     # 50ms fade between segments
voicecli generate script.md --plain         # strip [tags] and ignore <!-- directives -->
voicecli generate article.txt --chunked     # split into separate chunk files
voicecli generate article.txt --chunked --chunk-size 300  # ~20s chunks

# Voice cloning
voicecli clone "text" --ref voice.wav       # clone from reference audio
voicecli clone "text"                       # uses active sample (no --ref needed)
voicecli clone script.md --mp3              # from markdown + MP3 output
voicecli clone script.md --plain            # strip [tags] and ignore <!-- directives -->
voicecli clone article.txt --chunked        # split into separate chunk files

# Sample management
voicecli samples list                       # list all .wav in TTS/samples/
voicecli samples add file.wav               # import a WAV file
voicecli samples record name -d 30          # record 30s from mic (PulseAudio)
voicecli samples use name.wav               # set as active sample for clone
voicecli samples active                     # show current active sample
voicecli samples remove name.wav            # delete a sample

# Speech-to-text
voicecli transcribe audio.wav               # transcribe audio file
voicecli transcribe audio.wav --json        # JSON with language + timestamps
voicecli transcribe audio.wav -m large-v3   # choose model
voicecli transcribe audio.wav -l fr         # force language
voicecli listen                             # live mic → text (Kyutai)
voicecli listen -m 2.6b                     # English-only model

# Utilities
voicecli mp3 TTS/voices_out/file.wav       # convert WAV to MP3 (192kbps default)
voicecli mp3 TTS/voices_out/file.wav -b 320 # convert at 320kbps
voicecli voices                             # list Qwen voices
voicecli voices -e chatterbox               # list Chatterbox voices
voicecli engines                            # list available engines
voicecli emotions                           # emotion/expressiveness cheat sheet
```

## Unified Markdown Format

Write one `.md` file using ALL features — the translator adapts it per engine:

```markdown
---
language: French
accent: "Léger accent provençal"
personality: "Calme et douce"
emotion: "Chaleureuse"
exaggeration: 0.7
cfg_weight: 0.3
segment_gap: 200
crossfade: 50
---

Welcome everyone. [laugh] This is going to be fun!

<!-- emotion: "Passionnée et excitée", segment_gap: 500 -->
Now let me tell you something important. [sigh] It has been a long road.

<!-- language: Japanese, voice: Ono_Anna, crossfade: 0, segment_gap: 300 -->
A section in Japanese with a different voice.
```

### All frontmatter fields (all optional)

```yaml
---
language: French          # any language name (Qwen + Chatterbox Multilingual)
voice: Ryan               # built-in voice name (Qwen only)
engine: qwen              # qwen | chatterbox | chatterbox-turbo
instruct: "Parle avec colère" # raw instruct bypass (Qwen only) — overrides structured parts
accent: "Provençal"       # pronunciation/origin (Qwen, composes into instruct)
personality: "Calme"      # character traits (Qwen, composes into instruct)
speed: "Rythme posé"      # tempo/pace (Qwen, composes into instruct)
emotion: "Chaleureuse"    # emotional state (Qwen, composes into instruct)
exaggeration: 0.75        # expressiveness 0.25-2.0, default 0.5 (Chatterbox only)
cfg_weight: 0.3           # speaker adherence 0.0-1.0, default 0.5 (Chatterbox only)
segment_gap: 200          # ms silence between segments, default 0
crossfade: 50             # ms fade between segments, default 0
---
```

### Structured instruct composition

The `accent`, `personality`, `speed`, `emotion` fields auto-compose into `instruct`:
`"accent. personality. speed. emotion"` (only non-empty parts are joined).

- Per-section: `<!-- emotion: "Passionnée" -->` overrides just emotion, other parts inherited
- Raw `instruct` (frontmatter or directive) bypasses composition entirely
- Priority: raw `instruct` > composed parts
- **Write instruct parts in the target language** — French speech needs French instructs, English speech needs English instructs

### In-body directives

All frontmatter fields can also be set per-section using HTML comments. Multiple keys can be combined in a single comment, separated by commas:

- `<!-- emotion: "Passionnée", speed: "Rapide et haché" -->` — multi-key on one line
- `<!-- accent: "Parisien" -->` — single-key still works
- `<!-- instruct: "..." -->` — raw instruct bypass (Qwen, overrides all structured parts)
- `<!-- exaggeration: 0.8, cfg_weight: 0.3 -->` — per-section expressiveness (Chatterbox)
- `<!-- language: Japanese, voice: Ono_Anna -->` — per-section language + voice switch
- `<!-- segment_gap: 500, crossfade: 100 -->` — per-section transition control
- `[laugh]` `[chuckle]` `[sigh]` etc. — paralinguistic tags (native on Turbo, converted to instruct on Qwen, stripped on Multilingual)

Commas inside quoted values are handled correctly: `<!-- emotion: "Passionnée, mais contenue" -->`.

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
- Segment 1: "Welcome everyone." — instruct: "Léger accent provençal. Calme et douce. Chaleureuse, de plus en plus amusé, prêt à éclater de rire"
- Segment 2: "Ah ah ah ah ! This is going to be fun!" — instruct: "Léger accent provençal. Calme et douce. Chaleureuse, En riant, puis retrouve progressivement son calme"
- Segment 3: "Now let me tell you something important." — instruct: "Léger accent provençal. Calme et douce. Passionnée et excitée, avec une lassitude croissante"
- Segment 4: "Haaa... It has been a long road." — instruct: "Léger accent provençal. Calme et douce. Passionnée et excitée, En soupirant, puis reprend doucement contenance"
- exaggeration/cfg_weight: nulled
- Note: base instruct (from frontmatter/toml) is preserved in all tag-split segments

**Chatterbox Multilingual** (`tags: strip`, `segments: True`):
- Segment 1: "Welcome everyone. This is going to be fun!" — exaggeration: 0.7, language: French
- Segment 2: "Now let me tell you something important. It has been a long road." — segment_gap: 500
- instruct/accent/personality/emotion: nulled per-segment

**Chatterbox Turbo** (`tags: native`, `segments: True`):
- Segment 1: "Welcome everyone. [laugh] This is going to be fun!" — exaggeration: 0.7
- Segment 2: "Now let me tell you something important. [sigh] It has been a long road." — segment_gap: 500
- instruct/accent/personality/emotion: nulled per-segment, language: nulled

## Key Patterns

- Engines are lazy-loaded (model loaded on first use, not import)
- Engine registry in `engine.py:_get_registry()` — add new engines there
- `QWEN_ENGINES = frozenset({"qwen", "qwen-fast"})` in `engine.py` — single source for Qwen engine names
- Daemon (`daemon.py`) keeps Qwen models in VRAM; `generate`/`clone` try daemon first for Qwen engines, fall back silently to standalone if socket absent or any error occurs
- Daemon socket: `~/.local/share/voicecli/daemon.sock` (AF_UNIX, newline-delimited JSON)
- `generate` and `clone` both accept raw text OR a `.md` file path (auto-detected)
- `clone` falls back to active sample when `--ref` is omitted
- Priority chain: CLI flag > markdown frontmatter > voicecli.toml > hardcoded default
- Config backfill (`_apply_config_defaults`) runs after `parse_md_file()`, before `translate_for_engine()`
- Translation happens after config backfill but before field extraction in cli.py
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
