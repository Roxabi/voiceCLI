---
title: Backend Patterns
description: Python coding standards, design patterns, and best practices for voiceCLI
---

## 1.1 Code Organization

### Module Structure

```
src/voicecli/
├── cli.py              # Presentation — Typer commands
├── api.py              # Application — orchestration, public API
├── markdown.py         # Domain — data models, parsing
├── translate.py        # Domain — engine capability adaptation
├── config.py           # Domain — TOML configuration
├── utils.py            # Domain — shared utilities
├── engine.py           # Infrastructure — ABC + registry
├── engines/            # Infrastructure — concrete engines
│   ├── qwen.py
│   ├── qwen_fast.py
│   ├── chatterbox.py
│   └── chatterbox_turbo.py
├── daemon.py           # Infrastructure — model warm-keeping
├── samples.py          # Domain — voice sample management
├── transcribe.py       # Infrastructure — STT adapter
├── listen.py           # Infrastructure — real-time STT
└── __init__.py         # Public exports
```

> For the full layer model and dependency diagram, see [Architecture overview](../architecture/index).

### Rules

- **Flat module structure.** No nested packages beyond `engines/`. voiceCLI is a thin CLI — keep it flat.
- **One concern per module.** `markdown.py` owns parsing, `translate.py` owns adaptation, `config.py` owns config loading.
- **CLI handles presentation only.** Extract all orchestration into `api.py`. CLI commands parse flags, call API functions, and format output.
- **No cross-layer imports upward.** Domain modules never import from `api.py` or `cli.py`. Engines never import from `api.py`.

### File Naming

- **Modules:** snake_case — `chatterbox_turbo.py`, `stt_daemon.py`
- **Test files:** `test_<module>.py` — `test_markdown.py`, `test_translate.py`
- **Engine files:** match the engine name — `qwen.py`, `chatterbox.py`

### Anti-patterns

- Putting orchestration logic (config resolution, translation, engine dispatch) in `cli.py` command handlers.
- Creating utility grab-bags — `utils.py` exists but is focused (paths, audio, language codes). Don't dump unrelated helpers there.
- Importing engine classes directly in `api.py` — use `get_engine(name)` from the registry.

---

## 1.2 Design Patterns

### Codebase Patterns

| Pattern | Module | Description |
|---------|--------|-------------|
| Strategy + Registry | `engine.py` | `TTSEngine` ABC + `_get_registry()` factory for pluggable engines |
| Adapter / Translator | `translate.py` | `ENGINE_CAPS` matrix + `translate_for_engine()` adapts universal docs per engine |
| Composition | `markdown.py` | `compose_instruct()` builds instruct from structured parts |
| Context Manager Guard | `engine.py` | `cuda_guard()` catches CUDA errors and re-raises as `RuntimeError` |
| Dataclass Models | `markdown.py` | `TTSDocument` and `Segment` as `@dataclass` — simple, inspectable, serializable |
| Deferred Import | Throughout | Heavy libs imported inside function bodies, not at module level |

### Design Principles

- **Focused modules**: Each module does one thing. `translate.py` only translates. `config.py` only loads config.
- **Extension via registry**: Add new engines without modifying existing orchestration code. Register in `_get_registry()` and `ENGINE_CAPS`.
- **Abstraction via ABC**: `TTSEngine` defines the contract. Consumers depend on the abstraction, not concrete engines.
- **Pure domain logic**: `markdown.py` and `translate.py` are pure Python — no GPU, no I/O, no side effects. Easy to test.

---

## 1.3 Error Handling

### Layered Error Architecture

Use a two-layer approach: API exceptions and CLI error formatting.

**1. API layer** — raises standard Python exceptions:

```python
# api.py — raises descriptive exceptions, no CLI concerns
def _resolve_ref(ref: Path | str | None) -> Path:
    if ref is not None:
        ref = Path(ref)
        if not ref.exists():
            raise FileNotFoundError(f"Reference audio not found: {ref}")
        return ref
    active = get_active_path()
    if active is None:
        raise ValueError("No --ref provided and no active sample set.")
    return active
```

**2. CLI layer** — catches and formats for the terminal:

```python
# cli.py — catches API exceptions, formats user-friendly output
@app.command()
def clone_cmd(text: str, ...):
    try:
        result = api.clone(text, ref=ref, ...)
        typer.echo(f"Saved: {result.wav_path}")
    except (ValueError, FileNotFoundError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    except RuntimeError as e:
        # CUDA error — formatted diagnostic
        typer.echo(f"GPU error: {e}", err=True)
        raise typer.Exit(1)
```

### Exception Types

| Exception | When | Who Raises |
|-----------|------|------------|
| `ValueError` | Invalid engine name, no active sample, bad arguments | `api.py`, `engine.py` |
| `FileNotFoundError` | Missing ref audio, missing script file | `api.py` |
| `RuntimeError` | CUDA/GPU errors (via `cuda_guard`) | `engine.py` |

### Rules

- **API functions raise Python exceptions** — `ValueError`, `FileNotFoundError`, `RuntimeError`. Never `typer.Exit` or `SystemExit`.
- **CLI layer catches and formats** — `typer.echo()` + `typer.Exit(1)`.
- **`cuda_guard()` is the GPU error boundary** — catches CUDA-related `RuntimeError`/`OSError` and re-raises as `RuntimeError` with engine context.
- **Never raise raw `Exception`** — always use a specific type.

### Anti-patterns

- Raising `typer.Exit` from `api.py` — API must be CLI-agnostic.
- Catching exceptions and silently returning `None` — fail explicitly.
- Logging errors without context — always include engine name, file path, or operation.

---

## 1.4 Configuration Pattern

### Walk-Up Search

`config.py:load_defaults()` walks up from CWD to `$HOME` looking for `voicecli.toml`. If not found, returns empty dict with a stderr warning.

### Priority Chain

```
CLI flag / API kwarg  >  markdown frontmatter  >  voicecli.toml  >  hardcoded default
```

Enforced in `api.py:_resolve_config()`:

```python
r_engine = engine or cfg.get("engine", "qwen")      # API kwarg > toml > "qwen"
r_language = language or cfg.get("language", "English")  # API kwarg > toml > "English"
```

### Structured Instruct Composition

`voicecli.toml` can set structured parts (`accent`, `personality`, `speed`, `emotion`) that auto-compose into `instruct`. Raw `instruct` in toml bypasses composition.

### Rules

- **Config is read-only.** `load_defaults()` reads once, no mutation, no caching.
- **Unknown keys are silently ignored** — forward-compatible config.
- **Boolean coercion** — `_parse_bool()` handles `"true"`, `"false"`, `"1"`, `"0"`.
- **No config file is fine** — built-in defaults always work.

---

## 1.5 Import Discipline

### Heavy Imports Must Be Deferred

Any import that triggers torch, CUDA, model loading, or large dependencies must be deferred to the function body where it is first needed.

```python
# CORRECT — deferred to function body
def generate(self, text, voice, output_path, **kwargs):
    import torch
    from qwen_tts import Qwen3TTSModel
    # ...

# WRONG — module-level import blocks startup
import torch  # loads CUDA context on import
from qwen_tts import Qwen3TTSModel  # pulls in transformers
```

### Safe at Module Level

These imports are safe at the top of any module:

- Standard library (`pathlib`, `dataclasses`, `re`, `abc`, `typing`, etc.)
- `voicecli.markdown` (pure dataclasses + regex)
- `voicecli.config` (stdlib only)
- `voicecli.utils` (stdlib + lightweight — no torch)

### Must Be Deferred

- `torch`, `torchaudio`
- `qwen_tts`, `chatterbox`
- `transformers`, `soundfile`, `lameenc`
- `faster_whisper`, `moshi`
- Any engine class from `voicecli.engines.*`

---

## 1.6 SOLID Principles — Python / CLI

### SRP — Single Responsibility

Each module owns one concern. If a module imports from more than two other voicecli modules, consider whether it's doing too much.

| Module | Responsibility |
|--------|----------------|
| `cli.py` | CLI flags → API calls → formatted output |
| `api.py` | Config resolution → input parsing → engine dispatch |
| `markdown.py` | TTSDocument/Segment models + markdown parsing |
| `translate.py` | Engine capability matrix + document adaptation |
| `config.py` | TOML loading + walk-up search |

**Anti-pattern:** A module that does config loading, markdown parsing, and engine dispatch. That was `cli.py` before `api.py` was extracted.

### OCP — Open/Closed

The engine system is the canonical OCP example:

- **Open for extension:** Add `engines/new_engine.py`, register in `_get_registry()` and `ENGINE_CAPS`
- **Closed for modification:** `api.py`, `cli.py`, `translate.py` don't change when a new engine is added

**Anti-pattern:** Adding `if engine == "new_engine"` branches in `api.py` or `cli.py`.

### LSP — Liskov Substitution

All `TTSEngine` subclasses honor the contract:

- `generate()` returns a `Path` to the output WAV
- `clone()` returns a `Path` to the output WAV
- `list_voices()` returns a `list[str]`

`QwenFastEngine` extends `QwenEngine` by overriding model selection, not the interface contract. Any engine can be substituted without surprising the caller.

### ISP — Interface Segregation

`TTSEngine` has exactly 3 methods — no engine is forced to implement operations it doesn't need.

The public API exposes focused functions (`generate`, `clone`, `transcribe`), not a monolithic class with 20 methods.

### DIP — Dependency Inversion

`api.py` depends on the `TTSEngine` abstraction via `get_engine(name)`, not on concrete engine classes.

`translate.py` depends on `TTSDocument` (domain model), not on engine internals.

Engines depend on the ABC in `engine.py`, not on the orchestration layer.

**Note:** voiceCLI uses a simple registry function instead of a DI container. This is the right level of abstraction for a CLI tool — DI frameworks would add unnecessary complexity.

---

## 1.7 AI Quick Reference

Compressed imperative rules for AI agent consumption.

- Flat module structure — no nested packages beyond `engines/`
- One concern per module — don't mix parsing, config, and engine dispatch
- CLI handles presentation only — all orchestration in `api.py`
- API raises Python exceptions (`ValueError`, `FileNotFoundError`, `RuntimeError`) — never `typer.Exit`
- CLI catches API exceptions and formats with `typer.echo()`
- Heavy imports deferred to function bodies — never torch at module level
- New engines: create `engines/new.py`, register in `_get_registry()` + `ENGINE_CAPS`
- No cross-layer imports upward — domain never imports from api or cli
- Config priority: CLI kwarg > frontmatter > voicecli.toml > hardcoded default
- `TTSEngine` ABC has exactly 3 methods — `generate()`, `clone()`, `list_voices()`
- Deep copy before translation — `translate_for_engine()` never mutates the original
- Pure domain logic — `markdown.py` and `translate.py` have no I/O, no GPU deps
- `cuda_guard()` is the GPU error boundary — catches CUDA errors, re-raises as `RuntimeError`
- `compose_instruct()` joins structured parts — raw instruct bypasses composition

**SOLID:**

- SRP: one module per concern — if it imports >2 voicecli modules, it may be doing too much
- OCP: extend via registry + ENGINE_CAPS — no `if engine ==` branches in orchestration
- LSP: all engines honor TTSEngine contract — `generate()` and `clone()` return `Path`
- ISP: 3-method ABC — no unused operations forced on engines
- DIP: `api.py` uses `get_engine(name)` abstraction — never imports concrete engine classes
