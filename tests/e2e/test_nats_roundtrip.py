"""E2E round-trip test: publishes TTS+STT requests through docker-compose stack."""

from __future__ import annotations

from pathlib import Path

from tests.e2e.stub_hub import run_once


def test_nats_roundtrip(nkey_seed: tuple[Path, str], compose_stack: dict) -> None:
    """Full contract proof: hub ↔ TTS satellite, hub ↔ STT satellite."""
    seed_path, _ = nkey_seed
    run_once(seed_path=seed_path, nats_url="nats://localhost:4222")
