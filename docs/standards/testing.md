# Testing Standards

> Stub — fill in as test suite grows.

## Stack
- Framework: `pytest`
- Runner: `uv run pytest`
- Config: `pyproject.toml` `[tool.pytest.ini_options]`

## Conventions
- Unit tests in `tests/`
- Test file naming: `test_<module>.py`
- Use fixtures for shared setup
