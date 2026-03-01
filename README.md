# VoiceMe

Unified CLI for voice generation and transcription — Qwen3-TTS, Chatterbox, Faster Whisper & Kyutai STT backends.

## Requirements

- Python 3.11–3.12
- CUDA GPU (both engines run on GPU)
- [uv](https://docs.astral.sh/uv/) package manager

## Install

```bash
git clone <repo-url> && cd voiceMe
uv sync
```

## Quick Start

```bash
# Generate speech with default voice (Qwen, Ryan)
voiceme generate "Hello, how are you today?"

# Pick a different voice
voiceme generate "Bonjour" --voice Vivian --lang French

# Use Chatterbox engine
voiceme generate "This is exciting!" --engine chatterbox

# Clone a voice from a reference audio file
voiceme clone "Say this in my voice" --ref my_recording.wav

# Transcribe an audio file
voiceme transcribe recording.wav

# Live mic transcription
voiceme listen
```

## User Config (`voiceme.toml`)

Optional file at project root. Sets default values so you don't pass flags every time.

```toml
[defaults]
language = "French"
engine = "chatterbox"
exaggeration = 0.7
cfg_weight = 0.3
segment_gap = 200
crossfade = 50
```

Priority: **CLI flag > markdown frontmatter > voiceme.toml > hardcoded default**

## Commands

### `generate` — Text to speech

```bash
voiceme generate "Your text here"
voiceme generate "Your text" --engine chatterbox --output out.wav
voiceme generate script.md                      # read from markdown file
voiceme generate script.md --segment-gap 300    # 300ms silence between segments
voiceme generate script.md --crossfade 50       # 50ms fade between segments
```

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--engine` | `-e` | TTS engine (`qwen`, `chatterbox`, `chatterbox-turbo`) | `qwen` |
| `--voice` | `-v` | Voice name (Qwen only) | `Ono_Anna` |
| `--output` | `-o` | Output WAV path | auto-generated |
| `--lang` | | Language | `English` |
| `--mp3` | | Also save as MP3 | off |
| `--segment-gap` | | Silence between segments (ms) | `0` |
| `--crossfade` | | Fade between segments (ms) | `0` |

### `clone` — Voice cloning

```bash
voiceme clone "Text to speak" --ref reference.wav
voiceme clone "Text to speak"              # uses active sample (see below)
voiceme clone script.md --segment-gap 200  # with segment transitions
```

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--ref` | `-r` | Reference audio file | active sample |
| `--engine` | `-e` | TTS engine | `qwen` |
| `--ref-text` | | Transcript of reference audio | none |
| `--output` | `-o` | Output WAV path | auto-generated |
| `--lang` | | Language | `English` |
| `--mp3` | | Also save as MP3 | off |
| `--segment-gap` | | Silence between segments (ms) | `0` |
| `--crossfade` | | Fade between segments (ms) | `0` |

### `samples` — Manage voice samples

```bash
voiceme samples list                       # list all samples
voiceme samples add voice.wav              # import a WAV file
voiceme samples record my_voice            # record from microphone (10s)
voiceme samples record my_voice -d 5       # record for 5 seconds
voiceme samples use my_voice.wav           # set as active sample
voiceme samples active                     # show current active sample
voiceme samples remove my_voice.wav        # delete a sample
```

Once you set an active sample, `clone` uses it automatically — no `--ref` needed.

### `voices` — List available voices

```bash
voiceme voices                             # Qwen voices
voiceme voices --engine chatterbox
```

### `engines` — List available engines

```bash
voiceme engines
```

### `transcribe` — Speech to text

```bash
voiceme transcribe audio.wav                   # auto-detect language
voiceme transcribe audio.wav --lang fr         # force language
voiceme transcribe audio.wav --model large-v3  # choose model
voiceme transcribe audio.wav --json            # JSON with language + timestamps
voiceme transcribe audio.wav -o result.txt     # save to file
```

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--model` | `-m` | Whisper model | `large-v3-turbo` |
| `--lang` | `-l` | Force language code | auto-detect |
| `--output` | `-o` | Save text to file | `STT/texts_out/` |
| `--json` | | JSON output with timestamps | off |

Available models: `tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo`

### `listen` — Live mic transcription

```bash
voiceme listen                                 # EN + FR (1b model)
voiceme listen --model 2.6b                    # English-only, higher quality
```

Uses Kyutai STT for real-time mic-to-text. Press Ctrl+C to stop.

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--model` | `-m` | Kyutai model (`1b` or `2.6b`) | `1b` |

### `emotions` — Expressiveness cheat sheet

```bash
voiceme emotions
```

## Markdown File Input

Instead of raw text, `generate` and `clone` accept a `.md` file with YAML frontmatter:

```markdown
---
language: French
voice: Ryan
engine: qwen
instruct: "Parle avec un ton chaleureux et amical"
segment_gap: 200
crossfade: 50
---

Bonjour, comment allez-vous aujourd'hui ?

<!-- instruct: "Parle sérieusement" -->
<!-- segment_gap: 500 -->
Maintenant, parlons de choses importantes.
```

### Frontmatter fields

All optional — CLI flags override frontmatter values.

| Field | Engine | Description |
|-------|--------|-------------|
| `language` | qwen + chatterbox | Language for synthesis |
| `voice` | qwen | Speaker name |
| `engine` | all | `qwen`, `chatterbox`, or `chatterbox-turbo` |
| `instruct` | qwen | Free-form tone/emotion instruction |
| `exaggeration` | chatterbox | Expressiveness 0.25–2.0 (default 0.5) |
| `cfg_weight` | chatterbox | Speaker adherence 0.0–1.0 (default 0.5) |
| `segment_gap` | all | Silence between segments in ms (default 0) |
| `crossfade` | all | Fade between segments in ms (default 0) |

Markdown formatting (`# headers`, `**bold**`, `[links](url)`, etc.) is stripped automatically. Paralinguistic tags like `[laugh]` and `[sigh]` are preserved for Chatterbox Turbo.

### Per-section directives

All frontmatter fields can be overridden per-section using `<!-- key: value -->` HTML comments. Directives accumulate before a text block and apply to the text that follows. Each section inherits frontmatter defaults.

```markdown
---
language: French
instruct: "Parle chaleureusement"
exaggeration: 0.5
segment_gap: 200
---

Bienvenue à tous.

<!-- instruct: "Parle sérieusement" -->
<!-- exaggeration: 0.8 -->
<!-- segment_gap: 500 -->
Maintenant parlons de choses importantes.

<!-- language: Japanese -->
<!-- voice: Ono_Anna -->
<!-- crossfade: 100 -->
<!-- segment_gap: 0 -->
A section in Japanese with a different voice, crossfaded in.
```

Available directives: `instruct`, `exaggeration`, `cfg_weight`, `language`, `voice`, `segment_gap`, `crossfade`

### Segment transitions

| gap | crossfade | Result |
|-----|-----------|--------|
| 0   | 0         | Direct concat (default) |
| >0  | 0         | Hard cut, silence, hard cut |
| 0   | >0        | Fade-out then fade-in (no silence) |
| >0  | >0        | Fade-out, silence, fade-in |

> **Note:** Qwen clone does NOT support `instruct` — only `generate` does. All engines support per-section overrides for their supported parameters.

## Emotion Controls

**Qwen** — use `instruct` (free-form text):
- `"Speak angrily"`, `"Whispering"`, `"With excitement"`, `"Laughing, amused"`
- Works even when generating French speech — write instructions in English or French
- Can be set per-section via `<!-- instruct: "..." -->`

**Chatterbox Turbo** — paralinguistic tags (English only):
- Insert inline: `[laugh]`, `[chuckle]`, `[cough]`, `[sigh]`, `[gasp]`, `[groan]`, `[sniff]`, `[shush]`, `[clear throat]`
- Tags are converted to instruct on Qwen, stripped on Multilingual

**Chatterbox** — numeric controls (both turbo & multilingual):
- `exaggeration` (0.25–2.0): how expressive — can be set per-section
- `cfg_weight` (0.0–1.0): speaker adherence — can be set per-section
- Use 0.0 for cross-language cloning to reduce accent bleed

**Chatterbox Multilingual** — 23 languages:
Arabic, Danish, German, Greek, English, Spanish, Finnish, French, Hebrew, Hindi, Italian, Japanese, Korean, Malay, Dutch, Norwegian, Polish, Portuguese, Russian, Swedish, Swahili, Turkish, Chinese

## Available Voices (Qwen)

Vivian, Serena, Uncle_Fu, Dylan, Eric, Ryan, Aiden, Ono_Anna, Sohee

## Project Structure

```
voiceme.toml        # user config for default settings (optional)
TTS/
  texts_in/         # authored .md scripts (tracked in git)
  voices_out/       # generated WAV/MP3 (gitignored)
  samples/          # voice samples for cloning (gitignored)
STT/
  audio_in/         # audio files to transcribe (gitignored)
  texts_out/        # transcription results (gitignored)
src/voiceme/
  cli.py            # Typer commands (entry point)
  config.py         # TOML config loader (reads voiceme.toml)
  engine.py         # Abstract TTSEngine + registry
  engines/
    qwen.py         # Qwen3-TTS backend
    chatterbox.py   # Chatterbox Multilingual backend
    chatterbox_turbo.py  # Chatterbox Turbo backend
  markdown.py       # Frontmatter parser + directive parser
  translate.py      # Engine capability matrix + document translator
  utils.py          # Output paths + concat_audio + WAV→MP3
  samples.py        # Sample management (add/record/use/remove)
  transcribe.py     # Faster Whisper file transcription
  listen.py         # Kyutai STT real-time mic transcription
```
