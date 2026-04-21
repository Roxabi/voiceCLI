"""Repo-root pytest config — excludes tests/e2e/ from default collection.

The e2e suite requires Docker; CI opts in via `pytest tests/e2e/`.
"""

collect_ignore = ["tests/e2e"]
