"""Pytest fixtures for voiceCLI tests."""

import pytest


@pytest.fixture
def mock_engine(monkeypatch):
    """Register MockEngine in the registry for the test scope.

    This fixture patches the engine registry to include the mock engine,
    which is isolated to test scope (not in production code).
    """
    from voicecli.engine import _get_registry
    from tests.engines.mock import MockEngine

    # Get the current registry and add mock
    registry = _get_registry()
    registry["mock"] = MockEngine

    # Patch the registry function to return our modified registry
    def _patched_registry():
        # Return the cached registry with mock
        return registry

    monkeypatch.setattr("voicecli.engine._get_registry", _patched_registry)
    yield

    # Cleanup: remove mock from registry
    if "mock" in registry:
        del registry["mock"]
