---
title: Configuration
description: voicecli.toml reference and configuration resolution
---

## Config File

voiceCLI uses `voicecli.toml` (gitignored). Copy from `voicecli.example.toml` and customize.

### Discovery

`load_defaults()` walks up from CWD to `$HOME` looking for `voicecli.toml`. A config at `~/projects/voicecli.toml` is shared across all projects under `~/projects/`. If not found, a warning is printed to stderr and built-in defaults are used.

### Priority Chain

```
CLI flag / API kwarg  >  markdown frontmatter  >  voicecli.toml  >  hardcoded default
```

See [Backend Patterns — Configuration Pattern](./standards/backend-patterns#14-configuration-pattern) for implementation details.

### Reference

```toml
[defaults]
language = "French"
engine = "qwen"
accent = "Leger accent du sud provencal"
personality = "Voix calme, douce et flamboyante"
exaggeration = 0.7
cfg_weight = 0.3
segment_gap = 200       # ms silence between segments
crossfade = 50          # ms fade between segments
# plain = false         # strip [tags] and ignore <!-- directives -->
# chunked = false       # always output separate chunk files
# chunk_size = 500      # target chunk size in chars (~15 chars/sec)
```

### Structured Instruct Composition

`accent`, `personality`, `speed`, `emotion` auto-compose into `instruct`:

```
"accent. personality. speed. emotion"
```

Only non-empty parts are joined. Raw `instruct` bypasses composition entirely.

See [Ubiquitous Language — Instruct vs Structured Parts](./architecture/ubiquitous-language#instruct-vs-structured-parts) for the distinction.
