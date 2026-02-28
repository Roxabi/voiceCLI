# VoiceMe

Unified CLI for voice generation — Qwen3-TTS & Chatterbox backends.

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

### `emotions` — Expressiveness cheat sheet

```bash
voiceme emotions
```

## Markdown File Input

Instead of raw text, `generate` accepts a `.md` file with YAML frontmatter:

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

## Emotion Controls

**Qwen** — use `instruct` (free-form text):
- `"Speak angrily"`, `"Whispering"`, `"With excitement"`

**Chatterbox** — inline tags in text:
- `[laugh]`, `[chuckle]`, `[cough]`, `[sigh]`, `[gasp]`, `[groan]`, `[sniff]`, `[shush]`, `[clear throat]`

**Chatterbox** — numeric controls:
- `exaggeration` (0.25–2.0): how expressive
- `cfg_weight` (0.0–1.0): pacing tightness

## Available Voices (Qwen)

Vivian, Serena, Uncle_Fu, Dylan, Eric, Ryan, Aiden, Ono_Anna, Sohee

## Project Structure

```
src/voiceme/
  cli.py            # Typer commands (entry point)
  engine.py         # Abstract TTSEngine + registry
  engines/
    qwen.py         # Qwen3-TTS backend
    chatterbox.py   # Chatterbox backend
  samples.py        # Sample management (add/record/use/remove)
  markdown.py       # .md frontmatter parser + markdown stripper
  utils.py          # Output path helpers
```
