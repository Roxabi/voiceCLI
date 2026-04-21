"""Pytest fixtures for voiceCLI tests."""

import pytest


@pytest.fixture
def mock_engine(monkeypatch):
    """Activate MockEngine for the test scope via the env gate.

    Sets ``VOICECLI_ENABLE_MOCK_ENGINE=1`` for the duration of the test, which
    is the same gate the e2e docker-compose stack uses to expose ``mock`` in
    the engine registry and short-circuit the STT transcribe path.
    """
    monkeypatch.setenv("VOICECLI_ENABLE_MOCK_ENGINE", "1")
    yield
