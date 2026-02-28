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

## Commands

### `generate` — Text to speech

```bash
voiceme generate "Your text here"
voiceme generate "Your text" --engine chatterbox --output out.wav
voiceme generate script.md                 # read from markdown file
```

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--engine` | `-e` | TTS engine (`qwen` or `chatterbox`) | `qwen` |
| `--voice` | `-v` | Voice name (Qwen only) | `Ryan` |
| `--output` | `-o` | Output WAV path | auto-generated |
| `--lang` | | Language | `English` |

### `clone` — Voice cloning

```bash
voiceme clone "Text to speak" --ref reference.wav
voiceme clone "Text to speak"              # uses active sample (see below)
```

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--ref` | `-r` | Reference audio file | active sample |
| `--engine` | `-e` | TTS engine | `qwen` |
| `--ref-text` | | Transcript of reference audio | none |
| `--output` | `-o` | Output WAV path | auto-generated |
| `--lang` | | Language | `English` |

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
---

Bonjour, comment allez-vous aujourd'hui ?

Ceci est un deuxième paragraphe.
```

### Frontmatter fields

All optional — CLI flags override frontmatter values.

| Field | Engine | Description |
|-------|--------|-------------|
| `language` | both | Language for synthesis |
| `voice` | qwen | Speaker name |
| `engine` | both | `qwen` or `chatterbox` |
| `instruct` | qwen | Free-form tone/emotion instruction |
| `exaggeration` | chatterbox | Expressiveness 0.25–2.0 (default 0.5) |
| `cfg_weight` | chatterbox | Pacing control 0.0–1.0 (default 0.5) |

Markdown formatting (`# headers`, `**bold**`, `[links](url)`, etc.) is stripped automatically. Paralinguistic tags like `[laugh]` and `[sigh]` are preserved for Chatterbox.

### Multi-segment emotion control

Use `<!-- instruct: ... -->` HTML comments to vary the tone per section:

```markdown
---
language: French
engine: qwen
instruct: "Default calm tone"
---

<!-- instruct: Speak with gravitas and suspense -->
Le darwinisme et le socialisme ! Deux visions du monde...

<!-- instruct: Rising indignation and outrage -->
Et certains ont osé appliquer cette logique à la société humaine !

<!-- instruct: Warm, earnest tenderness -->
L'être humain ne survit pas seul. Il survit en groupe.
```

Each section is generated with its own emotion, then concatenated into a single audio file.

> **Note:** Multi-segment instruct only works with `voiceme generate` (built-in voices). The clone model does not support `instruct`.

## Emotion Controls

**Qwen** — use `instruct` (free-form text, English or Chinese):
- `"Speak angrily"`, `"Whispering"`, `"With excitement"`, `"Laughing, amused"`
- Works even when generating French speech — write instructions in English

**Chatterbox** — numeric controls (works in all 23 languages):
- `exaggeration` (0.25–2.0): how expressive
- `cfg_weight` (0.0–1.0): pacing tightness (use 0.0 for cross-language cloning to reduce accent bleed)

**Chatterbox** — supported languages:
Arabic, Danish, German, Greek, English, Spanish, Finnish, French, Hebrew, Hindi, Italian, Japanese, Korean, Malay, Dutch, Norwegian, Polish, Portuguese, Russian, Swedish, Swahili, Turkish, Chinese

### Known limitations

- **Qwen clone** (`voiceme clone`): does NOT support `instruct` — emotion control is not available when cloning a voice
- **Chatterbox**: paralinguistic tags (`[laugh]`, `[sigh]`) only work in the Turbo model, not the Multilingual model we ship

## Available Voices (Qwen)

Vivian, Serena, Uncle_Fu, Dylan, Eric, Ryan, Aiden, Ono_Anna, Sohee

## Project Structure

```
TTS/
  texts_in/         # authored .md scripts (tracked in git)
  voices_out/       # generated WAV/MP3 (gitignored)
  samples/          # voice samples for cloning (gitignored)
STT/
  audio_in/         # audio files to transcribe (gitignored)
  texts_out/        # transcription results (gitignored)
src/voiceme/
  cli.py            # Typer commands (entry point)
  engine.py         # Abstract TTSEngine + registry
  engines/
    qwen.py         # Qwen3-TTS backend
    chatterbox.py   # Chatterbox backend
  samples.py        # Sample management (add/record/use/remove)
  transcribe.py     # Faster Whisper file transcription
  listen.py         # Kyutai STT real-time mic transcription
  markdown.py       # .md frontmatter parser + markdown stripper
  translate.py      # Engine capability matrix + document translator
  utils.py          # Output path helpers
```
