# VoiceMe

Unified CLI for local voice generation with Qwen3-TTS and Chatterbox backends.

## Tech Stack

- Python 3.12, managed with `uv`
- CLI framework: Typer
- TTS engines: `qwen-tts` (Qwen3-TTS), `chatterbox-tts` (Chatterbox)
- Audio: `soundfile`, `lameenc` (MP3 encoding)
- Recording: PulseAudio CLI (`parecord`/`paplay`) — works on WSL2 via WSLg
- Linting: `ruff` (line-length 100, target py312)
- GPU: PyTorch 2.7+ cu128 for RTX 5070 Ti (Blackwell sm_120)

## Project Layout

```
src/voiceme/
  cli.py          — Typer app with all commands
  engine.py       — Abstract TTSEngine base class + engine registry
  engines/
    qwen.py       — Qwen3-TTS engine (CustomVoice for generate, Base for clone)
    chatterbox.py — Chatterbox engine (sentence chunking to avoid 40s cutoff)
  samples.py      — Sample management + PulseAudio recording with chimes
  markdown.py     — YAML frontmatter parser + markdown-to-plaintext stripper
  utils.py        — Output path helper + WAV→MP3 conversion
```

## All CLI Commands

```bash
# Speech generation (built-in voices)
voiceme generate "text"                    # Qwen default voice (Ryan)
voiceme generate "text" -e chatterbox      # Chatterbox engine
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

# Utilities
voiceme mp3 output/file.wav               # convert WAV to MP3 (192kbps default)
voiceme mp3 output/file.wav -b 320        # convert at 320kbps
voiceme voices                             # list Qwen voices
voiceme voices -e chatterbox               # list Chatterbox voices
voiceme engines                            # list available engines
voiceme emotions                           # emotion/expressiveness cheat sheet
```

## Markdown Frontmatter Fields

```yaml
---
language: French          # both engines
voice: Ryan               # qwen only
engine: qwen              # qwen | chatterbox
instruct: "Speak angrily" # qwen only — default instruct for all sections
exaggeration: 0.75        # chatterbox only (0.25-2.0, default 0.5)
cfg_weight: 0.3           # chatterbox only (0.0-1.0, default 0.5)
---
```

## Multi-Segment Instruct (Qwen generate only)

Use `<!-- instruct: ... -->` HTML comments to vary emotion per section:

```markdown
---
language: French
engine: qwen
instruct: "Default tone"
---

<!-- instruct: Speak with gravitas -->
First paragraph gets gravitas.

<!-- instruct: Laughing, amused -->
This part sounds amused.

<!-- instruct: Fierce intensity -->
Back to intensity here.
```

Each section is generated separately with its own instruct, then concatenated.

**Limitation:** Multi-segment instruct only works with `voiceme generate` (CustomVoice model).
The clone model (`generate_voice_clone`) does NOT support `instruct` — it is silently ignored.

## Key Patterns

- Engines are lazy-loaded (model loaded on first use, not import)
- Engine registry in `engine.py:_get_registry()` — add new engines there
- `generate` and `clone` both accept raw text OR a `.md` file path (auto-detected)
- `clone` falls back to active sample when `--ref` is omitted
- CLI flags always override frontmatter values
- Qwen clone uses `x_vector_only_mode=True` when no `--ref-text` is provided
- Qwen clone does NOT support `instruct` — only `generate` (CustomVoice) does
- Chatterbox uses the Multilingual model (23 languages including French)
- Chatterbox splits long text into sentence chunks (~250 chars) to avoid 40s cutoff
- Chatterbox clone defaults to cfg_weight=0.0 to reduce accent bleed in cross-language cloning
- Recommended Chatterbox settings: passionate speech = exaggeration 0.7-0.8, cfg_weight 0.3

## Conventions

- No over-engineering — this is a thin CLI, keep it flat and simple
- Imports of heavy libs (torch, qwen_tts, chatterbox) are deferred to function bodies
- Output WAVs/MP3s go to `output/` dir by default
- Samples stored in `samples/` dir
- Override conflicts in `[tool.uv] override-dependencies` in pyproject.toml
- Audio playback/recording uses PulseAudio CLI tools (paplay/parecord), not sounddevice
