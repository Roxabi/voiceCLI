---
title: Testing Standards
description: Pytest conventions, test structure, and mocking strategies for voiceCLI
---

## Stack

- **Framework:** pytest
- **Runner:** `uv run pytest`
- **Config:** `pyproject.toml` `[tool.pytest.ini_options]`

## Philosophy

voiceCLI has two distinct testing zones:

1. **Domain logic** (markdown parsing, translation, config loading, instruct composition) — pure Python, fast, no GPU needed. These should have high test coverage.
2. **Engine inference** (actual TTS generation, GPU operations) — slow, requires CUDA. These are integration tests run manually or in CI with GPU access.

Focus automated testing on zone 1. Zone 2 tests are opt-in.

## Test Structure

### File Organization

```
tests/
├── test_markdown.py        # TTSDocument, Segment, parse_md_file, compose_instruct
├── test_translate.py        # ENGINE_CAPS, translate_for_engine, tag handling
├── test_config.py           # load_defaults, priority chain, walk-up search
├── test_api.py              # _resolve_config, _resolve_input, _resolve_ref
├── test_utils.py            # smart_chunk, resolve_language, output paths
├── test_samples.py          # Sample management (filesystem mocked)
├── conftest.py              # Shared fixtures
```

### Naming

- Test files: `test_<module>.py` — matches source module
- Test functions: `test_<what_it_does>` — e.g., `test_parse_frontmatter_extracts_language`
- Fixtures: descriptive names — `sample_md_content`, `mock_config_dir`

### AAA Pattern

Every test follows **Arrange-Act-Assert**:

```python
def test_compose_instruct_joins_parts():
    # Arrange
    accent = "Provencal"
    personality = "Calm"

    # Act
    result = compose_instruct(accent=accent, personality=personality)

    # Assert
    assert result == "Provencal. Calm"
```

Keep each section short. If Arrange is complex, use a fixture.

## What to Test

### High Priority — Domain Logic

| Module | What to Test |
|--------|-------------|
| `markdown.py` | Frontmatter parsing, segment splitting, directive parsing, instruct composition, markdown stripping |
| `translate.py` | Tag modes (strip, native, to_instruct), field nulling per engine, deep copy preservation, segment splitting at tags |
| `config.py` | TOML loading, walk-up search, boolean coercion, missing file handling |
| `api.py` | Config resolution priority chain, input file detection, config backfill, ref resolution |
| `utils.py` | Text chunking, language code resolution, output path generation |

### Medium Priority — Integration

| Module | What to Test |
|--------|-------------|
| `samples.py` | Sample listing, active sample tracking (with tmp filesystem) |
| `api.py` | Full `generate()` / `clone()` flow with mocked engine |

### Low Priority — GPU (Manual / CI with GPU)

| Module | What to Test |
|--------|-------------|
| `engines/*.py` | Actual audio generation, model loading, voice cloning |
| `daemon.py` | Socket communication, request/response cycle |

## Mocking Strategies

### Mocking Engines

Mock at the registry level to avoid loading torch:

```python
from unittest.mock import MagicMock, patch

@patch("voicecli.engine._get_registry")
def test_generate_calls_engine(mock_registry):
    mock_engine = MagicMock()
    mock_engine.generate.return_value = Path("/tmp/test.wav")
    mock_registry.return_value = {"qwen": lambda: mock_engine}

    result = generate("Hello", engine="qwen")
    mock_engine.generate.assert_called_once()
```

### Mocking Config

Use `tmp_path` to create temporary TOML files:

```python
def test_load_defaults_reads_toml(tmp_path):
    toml_file = tmp_path / "voicecli.toml"
    toml_file.write_text('[defaults]\nlanguage = "French"\n')

    with patch("voicecli.config._find_config", return_value=toml_file):
        cfg = load_defaults()
    assert cfg["language"] == "French"
```

### Mocking Filesystem

Use `tmp_path` for sample management and output paths:

```python
def test_resolve_ref_finds_file(tmp_path):
    ref = tmp_path / "voice.wav"
    ref.touch()

    result = _resolve_ref(ref)
    assert result == ref
```

### Mocking Daemon

Mock at the socket level — don't start a real daemon:

```python
@patch("voicecli.api.SOCKET_PATH")
@patch("voicecli.api.daemon_request")
def test_try_daemon_returns_path(mock_request, mock_socket):
    mock_socket.exists.return_value = True
    mock_request.return_value = {"status": "ok", "path": "/tmp/out.wav"}

    result = _try_daemon({"action": "generate"})
    assert result == Path("/tmp/out.wav")
```

## Fixtures

### Common Fixtures in `conftest.py`

```python
import pytest
from voicecli.markdown import TTSDocument, Segment

@pytest.fixture
def simple_doc():
    """A minimal TTSDocument for testing."""
    return TTSDocument(text="Hello world", language="English")

@pytest.fixture
def segmented_doc():
    """A TTSDocument with multiple segments."""
    return TTSDocument(
        text="Hello world. Goodbye world.",
        language="French",
        instruct="Calm",
        segments=[
            Segment(text="Hello world.", instruct="Calm", language="French"),
            Segment(text="Goodbye world.", instruct="Excited", language="French"),
        ],
    )

@pytest.fixture
def sample_md(tmp_path):
    """A temporary .md file with frontmatter."""
    md = tmp_path / "test.md"
    md.write_text('---\nlanguage: French\n---\nHello world.\n')
    return md
```

## Rules

- **Test domain logic thoroughly** — markdown, translate, config are the core and should have high coverage.
- **Don't test engine internals** in unit tests — mock the engine registry.
- **Use `tmp_path`** for all filesystem operations — never write to real project directories.
- **One assertion focus per test** — multiple assertions are fine if they test the same behavior.
- **No GPU in CI** unless explicitly configured — mark GPU tests with `@pytest.mark.gpu`.
- **Test the priority chain** — verify that kwargs override frontmatter override toml override defaults.
- **Test translation per engine** — verify that unsupported fields are nulled, tags are handled correctly.

## Anti-patterns

- Testing engine output quality (audio content) — that's manual evaluation, not automated testing.
- Creating test fixtures that require torch or model loading.
- Testing private functions directly when the public API covers the behavior.
- Mocking too deep — mock at the boundary (registry, config file, filesystem), not internal functions.
- Tests that depend on CWD or system state — use `tmp_path` and explicit paths.

## AI Quick Reference

- pytest + `uv run pytest`
- Tests in `tests/test_<module>.py`
- AAA pattern: Arrange-Act-Assert
- Mock engines at registry level — `@patch("voicecli.engine._get_registry")`
- Mock config with `tmp_path` + `_find_config` patch
- Mock daemon at socket level — never start real daemon in tests
- High priority: markdown parsing, translation, config resolution
- Skip GPU tests in CI — `@pytest.mark.gpu`
- Use fixtures for common TTSDocument/Segment objects
- Test priority chain explicitly: kwargs > frontmatter > toml > defaults
