# VoiceMe

Unified CLI for local voice generation with Qwen3-TTS and Chatterbox backends.

## Tech Stack

- Python 3.12, managed with `uv`
- CLI framework: Typer
- TTS engines: `qwen-tts` (Qwen3-TTS), `chatterbox-tts` (Chatterbox)
- Audio: `soundfile`, `torchaudio`, `sounddevice` (recording)
- Linting: `ruff` (line-length 100, target py312)

## Project Layout

```
src/voiceme/
  cli.py          — Typer app with all commands (generate, clone, samples, voices, engines, emotions)
  engine.py       — Abstract TTSEngine base class + engine registry
  engines/
    qwen.py       — Qwen3-TTS engine (CustomVoice for generate, Base for clone)
    chatterbox.py — Chatterbox engine
  samples.py      — Sample management (list/add/record/use/active/remove), state in samples/.active
  markdown.py     — YAML frontmatter parser + markdown-to-plaintext stripper
  utils.py        — Output path helper
```

## Key Patterns

- Engines are lazy-loaded (model loaded on first use, not import)
- Engine registry in `engine.py:_get_registry()` — add new engines there
- `generate` accepts raw text OR a `.md` file path (auto-detected by extension)
- `clone` falls back to active sample when `--ref` is omitted
- Frontmatter fields: language, voice, engine, instruct (qwen), exaggeration/cfg_weight (chatterbox)
- CLI flags always override frontmatter values

## Commands

```bash
uv run voiceme generate "text"       # TTS with built-in voice
uv run voiceme generate script.md    # TTS from markdown file
uv run voiceme clone "text" --ref x  # voice cloning
uv run voiceme clone "text"          # uses active sample
uv run voiceme samples list|add|record|use|active|remove
uv run voiceme voices [--engine]
uv run voiceme engines
uv run voiceme emotions
```

## Conventions

- No over-engineering — this is a thin CLI, keep it flat and simple
- Imports of heavy libs (torch, qwen_tts, chatterbox) are deferred to function bodies
- Output WAVs go to `output/` dir by default
- Samples stored in `samples/` dir
- Override conflicts in `[tool.uv] override-dependencies` in pyproject.toml
