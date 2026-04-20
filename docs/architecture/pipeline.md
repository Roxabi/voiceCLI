# Code Pipeline

```
User runs: voicecli generate script.md -e chatterbox

  1. cli.py         — detects .md input, resolves engine from CLI flag / frontmatter
  2. markdown.py    — parses YAML frontmatter + body into TTSDocument
                      (extracts instruct, segments, tags, exaggeration, etc.)
  2b. api.py        — _apply_config_defaults(): backfills voicecli.toml structured parts
                      (accent, personality, speed, emotion) into doc/segments, recomposes instruct
  3. translate.py   — adapts TTSDocument for the target engine via ENGINE_CAPS matrix
                      (strips/converts tags, nulls unsupported fields)
  4. api.py         — extracts fields from translated doc into engine kwargs
  5. engine/*.py    — generates audio (chunking, model inference, WAV output)
  6. utils.py       — optional MP3 conversion
```

Each step is pure Python, no LLM involved. The translator is the key piece — it makes one universal `.md` file work across all engines without manual adaptation.

## LLM skill (`skills/voice/SKILL.md`)

The `/voicecli` skill lives at `skills/voice/SKILL.md` (source of truth, part of the self-contained plugin).

Install directly:

```bash
claude plugin marketplace add Roxabi/voiceCLI && claude plugin install voice-cli
```

The LLM does NOT translate documents — that is handled by `translate.py` in the code pipeline. The skill just needs to know that unified format exists so it can write scripts using all features.
