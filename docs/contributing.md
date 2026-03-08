---
title: Contributing
description: Guidelines for contributing to voiceCLI
---

See `CONTRIBUTING.md` in the project root for setup instructions and contribution workflow.

## Quick Reference

- **Branch strategy:** Feature branches → PR against `staging` → promote to `main`
- **Linting:** `uv run ruff check .` and `uv run ruff format .` (line-length 100, target py312)
- **Tests:** `uv run pytest` — see [Testing Standards](./standards/testing) for conventions
- **Code review:** [Code Review Standards](./standards/code-review) — Conventional Comments, CI green before merge

## Architecture Docs

Before making structural changes, read:

- [Architecture overview](./architecture/index) — layers, dependency flow, module map
- [Architectural Patterns](./architecture/patterns) — strategy, adapter, pipeline, SOLID
- [Ubiquitous Language](./architecture/ubiquitous-language) — domain glossary
- [Backend Patterns](./standards/backend-patterns) — coding conventions and rules

## Adding a New Engine

1. Create `src/voicecli/engines/new_engine.py` implementing `TTSEngine`
2. Register in `engine.py:_get_registry()`
3. Add capability entry to `translate.py:ENGINE_CAPS`
4. No changes needed in `api.py` or `cli.py`

See [Strategy + Registry Pattern](./architecture/patterns#strategy--registry-pattern) for details.
