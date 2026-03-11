# Contributing

## Workflow

All development goes through the `staging` branch. `main` is the stable release branch.

```
feature/fix branch → PR → staging → (promote) → main
```

1. Create a branch from `staging`: `feat/daemon-reconnect`, `fix/mp3-bitrate`
2. Open a PR targeting `staging`
3. Pass CI (lint, tests)
4. Merge

## Commit conventions

[Conventional Commits](https://www.conventionalcommits.org/):

```
feat(engine): add chatterbox turbo backend
fix(markdown): handle empty frontmatter gracefully
chore: bump qwen-tts to 1.2
test(translate): cover tag-to-instruct edge cases
refactor(cli): extract config backfill logic
```

- Scope is optional but encouraged (`cli`, `engine`, `markdown`, `translate`, `config`, `samples`, `stt`, `overlay`, `dictate`)
- Breaking changes: add `!` after scope — `feat(cli)!: rename generate flags`

## PR conventions

- Title = Conventional Commits format (becomes the merge commit)
- Link the issue: `Closes #12`
- One logical change per PR
- All checks must pass before merge

## Code style

```bash
uv run ruff check .       # lint — must pass
uv run ruff format .      # format — auto-fix
uv run pytest             # tests — must pass
```

Config: line-length 100, target py312 (see `pyproject.toml`).

## Testing

- Tests live in `tests/`
- Run with `uv run pytest`
- Mock heavy dependencies (torch, qwen_tts, chatterbox) — don't require GPU in tests

## Project structure

See [README.md](README.md) for the full project tree.
