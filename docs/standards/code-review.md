---
title: Code Review Standards
description: Review checklist, conventional comments, and approval criteria for voiceCLI
---

## Process

- All changes via PR against `staging`
- Use Conventional Comments for feedback
- CI must be green before merge
- PRs should be focused — prefer small, reviewable changes over large omnibus PRs

## Review Checklist

### Correctness

- [ ] Edge cases handled (empty text, missing config, no segments)
- [ ] Error paths raise the right exception type (`ValueError`, `FileNotFoundError`, `RuntimeError`)
- [ ] Priority chain respected (kwargs > frontmatter > toml > defaults)
- [ ] Deep copy used before mutation (especially in `translate.py`)
- [ ] Path handling works with both `str` and `Path` inputs

### Architecture

- [ ] No cross-layer imports upward (domain doesn't import from api/cli)
- [ ] New engine registered in both `_get_registry()` and `ENGINE_CAPS`
- [ ] Heavy imports deferred to function bodies (no torch at module level)
- [ ] CLI commands delegate to `api.py` — no orchestration in cli.py
- [ ] API functions raise exceptions, not `typer.Exit`

### Performance

- [ ] No unnecessary model loading (lazy initialization preserved)
- [ ] Large text properly chunked for engine limits
- [ ] Daemon fallback is transparent (no error on absent socket)

### Security

- [ ] No secrets in committed files (config paths, API keys)
- [ ] File paths validated before use (no path traversal)
- [ ] User input sanitized at CLI boundary

### Tests

- [ ] Domain logic changes have corresponding tests
- [ ] Tests use `tmp_path` — no writes to real project directories
- [ ] Engine tests mock at registry level — no GPU required
- [ ] Priority chain tested if config resolution changed

### Readability

- [ ] Module stays focused on its single concern
- [ ] No over-engineering — simple solutions preferred
- [ ] Comments explain "why", not "what" — only where logic isn't self-evident
- [ ] Consistent naming with existing codebase conventions

## Review Criteria by Area

### Engine Changes (`engines/*.py`, `engine.py`)

- Implements all 3 `TTSEngine` methods (`generate`, `clone`, `list_voices`)
- LSP honored — return types match ABC contract
- Heavy imports deferred inside methods
- `cuda_guard()` used for GPU operations
- Capability entry added to `ENGINE_CAPS` in `translate.py`

### Translation Changes (`translate.py`)

- `ENGINE_CAPS` updated if new capability added
- Deep copy preserved — original document never mutated
- Tag handling modes tested for all engines
- New tag data has entries in both EN and FR maps

### Markdown Changes (`markdown.py`)

- Frontmatter parsing handles missing/malformed fields gracefully
- Directive parsing handles quoted commas correctly
- Segment inheritance from frontmatter defaults verified
- `compose_instruct()` bypass logic preserved for raw instruct

### API Changes (`api.py`)

- Priority chain enforced correctly
- Config backfill runs after parsing, before translation
- Daemon fallback is silent (returns `None`, doesn't raise)
- `TTSResult` fields populated correctly (wav_path, mp3_path, chunk_paths)
- Both `str` and `Path` accepted for all path parameters

### CLI Changes (`cli.py`)

- Delegates to `api.py` functions — no inline orchestration
- Catches API exceptions and formats with `typer.echo()`
- Exit codes: 0 for success, 1 for errors
- Output messages are concise and actionable

## Conventional Comments

Use labeled comments with severity decorators:

| Label | When |
|-------|------|
| `praise:` | Something done well |
| `nitpick:` | Minor style preference |
| `suggestion:` | Improvement idea, take it or leave it |
| `issue:` | Something that needs to be fixed |
| `question:` | Need clarification |
| `thought:` | Observation for future consideration |
| `todo:` | Follow-up task to track |

### Severity Decorators

- `(blocking)` — Must be resolved before merge
- `(non-blocking)` — Can be addressed in a follow-up

### Examples

```
praise: Clean separation — config backfill in its own function makes testing easy.

issue (blocking): This mutates the original TTSDocument. Use deepcopy() first.

suggestion (non-blocking): Consider extracting this into a helper — it's used in both generate and clone paths.

nitpick: Prefer `Path` over string concatenation for file paths.
```

## Approval Criteria

| Situation | Action |
|-----------|--------|
| All checks pass, no blocking issues | Approve |
| Minor nitpicks only | Approve with comments |
| Blocking architecture issue | Request changes |
| Missing tests for domain logic changes | Request changes |
| Heavy import at module level | Request changes |
| Cross-layer import upward | Request changes |

## AI Quick Reference

- PRs against `staging`, CI green before merge
- Conventional Comments with `(blocking)` / `(non-blocking)` decorators
- Block on: cross-layer imports, heavy imports at module level, missing deepcopy, API raising typer.Exit
- Approve with comments on: nitpicks, minor naming, style preferences
- Always check: priority chain, ENGINE_CAPS consistency, lazy loading preserved
- Engine changes need: all 3 ABC methods, ENGINE_CAPS entry, cuda_guard usage
- Translation changes need: deep copy, all tag modes tested, EN+FR tag data
