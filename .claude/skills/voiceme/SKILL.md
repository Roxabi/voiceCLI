---
description: "VoiceMe — generate speech, clone voices, manage samples, and convert audio. Triggers: voiceme, generate speech, clone voice, voice sample, text to speech, TTS"
user-invocable: true
argument-description: "Command and arguments (e.g. 'generate Hello world', 'clone text --engine qwen', 'samples list')"
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
---

# VoiceMe CLI Skill

You are helping the user with the VoiceMe voice generation CLI. Run commands via `uv run voiceme` from the project root at `/home/mickael/projects/voiceMe`.

## Available Commands

### Generate speech (built-in voice)
```bash
uv run voiceme generate "Text to speak" --engine qwen --voice Chelsie --lang English
uv run voiceme generate script.md              # from markdown file with frontmatter
uv run voiceme generate "Hello" --mp3          # also save as MP3
```

### Clone a voice
```bash
uv run voiceme clone "Text" --ref samples/my_voice.wav --engine qwen
uv run voiceme clone "Text"                    # uses active sample
uv run voiceme clone "Text" --mp3              # also save as MP3
```

### Sample management
```bash
uv run voiceme samples list                    # list all samples
uv run voiceme samples add /path/to/file.wav   # import a WAV file
uv run voiceme samples record my_voice -d 15   # record 15s from mic
uv run voiceme samples use my_voice.wav        # set as active sample
uv run voiceme samples active                  # show active sample
uv run voiceme samples remove old_sample.wav   # delete a sample
```

### Utilities
```bash
uv run voiceme mp3 output/file.wav             # convert WAV to MP3
uv run voiceme voices --engine qwen            # list voices for engine
uv run voiceme engines                         # list available engines
uv run voiceme emotions                        # show emotion controls cheat sheet
```

## Markdown Frontmatter Format

Create `.md` files in `scripts/` with YAML frontmatter:

```markdown
---
language: French
engine: qwen
voice: Chelsie
instruct: "Speak with warmth and conviction"
---

Your text to synthesize goes here.
```

### Frontmatter fields (all optional)
| Field | Description | Engine |
|-------|-------------|--------|
| `language` | Language name | both |
| `engine` | `qwen` or `chatterbox` | both |
| `voice` | Speaker name | qwen only |
| `instruct` | Free-form tone/emotion instruction | qwen only |
| `exaggeration` | Expressiveness 0.25-2.0 (default 0.5) | chatterbox only |
| `cfg_weight` | Pacing control 0.0-1.0 (default 0.5) | chatterbox only |

## Multi-Segment Emotion (Qwen generate only)

Use `<!-- instruct: ... -->` HTML comments for per-section emotion:

```markdown
---
language: French
engine: qwen
---

<!-- instruct: Speak with gravitas -->
First paragraph.

<!-- instruct: Laughing, amused -->
Second paragraph.
```

## Engine Notes

- **Qwen generate**: Supports built-in voices + `instruct` for emotion (including multi-segment). French works great.
- **Qwen clone**: Voice cloning works but does NOT support `instruct` — emotion control unavailable.
- **Chatterbox**: English-only base model. Paralinguistic tags (`[laugh]`, `[sigh]`) only work in Turbo model. For passionate speech: exaggeration 0.7-0.8, cfg_weight 0.3.

## Handling the user's request

If the user provided arguments: `$ARGUMENTS`

Parse the user's intent and run the appropriate `uv run voiceme` command. If they ask to generate or clone without specifying details, help them choose the right options. If they want to create a script, write a `.md` file in `scripts/` with appropriate frontmatter.

Output files are saved to `output/` by default.
