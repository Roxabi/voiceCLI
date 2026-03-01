---
description: "VoiceMe — generate speech, clone voices, transcribe audio, live dictation, manage samples, and convert audio. Triggers: voiceme, generate speech, clone voice, voice sample, text to speech, TTS, transcribe, STT, speech to text, dictation, listen"
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
uv run voiceme generate script.md --segment-gap 300   # 300ms silence between segments
uv run voiceme generate script.md --crossfade 50      # 50ms fade between segments
uv run voiceme generate "Long text" --chunked  # output each chunk as a separate file
uv run voiceme generate "Long text" --chunked --chunk-size 300  # smaller chunks (~20s each)
```

### Clone a voice
```bash
uv run voiceme clone "Text" --ref TTS/samples/my_voice.wav --engine qwen
uv run voiceme clone "Text"                    # uses active sample
uv run voiceme clone script.md --segment-gap 200      # with segment transitions
uv run voiceme clone "Long text" --chunked     # chunked clone output
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

### Transcribe audio (Faster Whisper)
```bash
uv run voiceme transcribe audio.wav                  # transcribe, auto-detect language
uv run voiceme transcribe audio.wav --lang fr        # force language
uv run voiceme transcribe audio.wav -m large-v3      # choose model
uv run voiceme transcribe audio.wav --json           # JSON with language + timestamps
uv run voiceme transcribe audio.wav -o result.txt    # save to file
```

### Live mic transcription (Kyutai STT)
```bash
uv run voiceme listen                                # live mic → text, Ctrl+C to stop
uv run voiceme listen -m 2.6b                        # English-only 2.6b model
```

### Utilities
```bash
uv run voiceme mp3 TTS/voices_out/file.wav      # convert WAV to MP3
uv run voiceme voices --engine qwen            # list voices for engine
uv run voiceme engines                         # list available engines
uv run voiceme emotions                        # show emotion controls cheat sheet
```

## User Config (`voiceme.toml`)

Optional file at project root for default settings:

```toml
[defaults]
language = "French"
engine = "qwen"
accent = "Léger accent du sud provençal"
personality = "Voix calme, douce et flamboyante"
exaggeration = 0.7
cfg_weight = 0.3
segment_gap = 200
crossfade = 50
```

Structured instruct parts (`accent`, `personality`, `speed`, `emotion`) auto-compose into `instruct`.
Raw `instruct` bypasses composition. **Write instruct parts in the target language.**

**Segment propagation**: toml structured parts are backfilled into `.md` segments where frontmatter
didn't set them, so a script with no frontmatter still inherits instruct from voiceme.toml.

Priority: **CLI flag > markdown frontmatter > voiceme.toml > hardcoded default**

## Markdown Frontmatter Format

Create `.md` files in `TTS/texts_in/` with YAML frontmatter:

```markdown
---
language: French
engine: qwen
accent: "Léger accent provençal"
personality: "Calme et douce"
emotion: "Chaleureuse"
segment_gap: 200
crossfade: 50
---

Your text to synthesize goes here.
```

### Frontmatter fields (all optional)
| Field | Description | Engine |
|-------|-------------|--------|
| `language` | Language name | qwen + chatterbox |
| `engine` | `qwen`, `chatterbox`, or `chatterbox-turbo` | all |
| `voice` | Speaker name | qwen only |
| `accent` | Pronunciation/regional origin (composes into instruct) | qwen only |
| `personality` | Character traits (composes into instruct) | qwen only |
| `speed` | Tempo/pace (composes into instruct) | qwen only |
| `emotion` | Emotional state (composes into instruct) | qwen only |
| `instruct` | Raw instruct bypass (overrides structured parts) | qwen only |
| `exaggeration` | Expressiveness 0.25-2.0 (default 0.5) | chatterbox only |
| `cfg_weight` | Speaker adherence 0.0-1.0 (default 0.5) | chatterbox only |
| `segment_gap` | Silence between segments (ms, default 0) | all |
| `crossfade` | Fade between segments (ms, default 0) | all |

## Per-Section Directives

All frontmatter fields can be overridden per-section using `<!-- key: value -->` HTML comments. Multiple keys can be combined in a single comment, separated by commas. Directives accumulate before a text block and apply to the text that follows. Each section inherits frontmatter defaults. Commas inside quoted values are safe: `<!-- emotion: "Passionnée, mais contenue" -->`.

```markdown
---
language: French
accent: "Provençal"
personality: "Calme et douce"
emotion: "Chaleureuse"
exaggeration: 0.5
segment_gap: 200
---

Bienvenue à tous.

<!-- emotion: "Passionnée", exaggeration: 0.8, segment_gap: 500 -->
Maintenant parlons de choses importantes.

<!-- language: Japanese, voice: Ono_Anna, crossfade: 100, segment_gap: 0 -->
A section in Japanese with a different voice, crossfaded in.
```

### Available inline directives
`accent`, `personality`, `speed`, `emotion`, `instruct`, `exaggeration`, `cfg_weight`, `language`, `voice`, `segment_gap`, `crossfade`

### Segment transition modes

| gap | crossfade | Result |
|-----|-----------|--------|
| 0   | 0         | Direct concat (default) |
| >0  | 0         | Hard cut, silence, hard cut |
| 0   | >0        | Fade-out then fade-in (no silence) |
| >0  | >0        | Fade-out, silence, fade-in |

## Engine Notes

- **Qwen generate**: Supports built-in voices + `instruct` for emotion. Per-section language/voice changes. French works great.
- **Qwen clone**: Voice cloning works but does NOT support `instruct` — emotion control unavailable.
- **Chatterbox Multilingual** (`-e chatterbox`): 23 languages. No paralinguistic tags — use exaggeration/cfg_weight for expressiveness. Per-section exaggeration/cfg_weight/language. For passionate speech: exaggeration 0.7-0.8, cfg_weight 0.3.
- **Chatterbox Turbo** (`-e chatterbox-turbo`): English-only. Supports paralinguistic tags inline: `[laugh]`, `[chuckle]`, `[cough]`, `[sigh]`, `[gasp]`, `[groan]`, `[sniff]`, `[shush]`, `[clear throat]`. Per-section exaggeration/cfg_weight.

**Unified format**: Scripts can use ALL features (tags, directives, exaggeration, language, segment transitions) simultaneously. The translator (`translate.py`) automatically adapts the document for the target engine — unsupported fields are nulled per-segment. All engines support per-section overrides and configurable segment transitions (gap, crossfade, or both). Base instruct is preserved in tag-split segments (e.g. `[laugh]` on Qwen keeps the original instruct alongside the tag instruct).

## STT Notes

- **Faster Whisper** (`transcribe`): File-based transcription. 99 languages, auto-detection. Default model `large-v3-turbo` (fast, accurate, ~6GB VRAM). Models: tiny, base, small, medium, large-v3, large-v3-turbo.
- **Kyutai STT** (`listen`): Real-time mic transcription. Model `1b` = EN+FR, model `2.6b` = EN-only (higher quality). Uses PulseAudio for mic input (record-then-transcribe loop).

## Chunked Output

Use `--chunked` for long texts to enable progressive sending via Telegram. Each chunk is saved as a separate numbered file (`prefix_001.wav`, `prefix_002.wav`...) and a `.done` sentinel is written when complete.

- For plain text: splits at paragraph/sentence boundaries (~500 chars per chunk by default)
- For .md scripts with segments: each segment becomes a chunk
- `--chunk-size N` adjusts target chunk size in characters (~15 chars/sec of speech)

**Always use `--chunked` when generating from Telegram** so the bot can send audio progressively.

Output format is WAV by default. For MP3, configure it in `voiceme.toml` or use `--mp3` explicitly. Do NOT add `--mp3` unless the user asks for it.

## Handling the user's request

If the user provided arguments: `$ARGUMENTS`

Parse the user's intent and run the appropriate `uv run voiceme` command. If they ask to generate or clone without specifying details, help them choose the right options. If they want to create a script, write a `.md` file in `TTS/texts_in/` with appropriate frontmatter.

Output files are saved to `TTS/voices_out/` by default. Transcription results are saved to `STT/texts_out/` by default.
