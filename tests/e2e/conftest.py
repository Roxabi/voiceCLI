"""Session-scoped fixtures for NATS round-trip E2E.

Requires Docker; on machines without Docker the compose_stack fixture skips.
The tests/e2e/ directory is excluded from default pytest collection by the
repo-root conftest.py (collect_ignore = ["tests/e2e"]) — CI opts in via
`pytest tests/e2e/`.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import nkeys
import pytest

E2E_DIR = Path(__file__).parent
REPO_ROOT = E2E_DIR.parent.parent
NATS_TEST_DIR = REPO_ROOT / "tests" / "nats"
# Allow e2e tests to import shared fakes from tests/nats/_fakes.py
sys.path.insert(0, str(NATS_TEST_DIR))

COMPOSE_FILE = E2E_DIR / "docker-compose.nats.yml"
CONF_TEMPLATE = E2E_DIR / "nats-server.conf.template"

READINESS_TIMEOUT = 30.0
READINESS_INTERVAL = 0.5


def _poll_nats(host: str, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError:
            time.sleep(READINESS_INTERVAL)
    raise TimeoutError(f"NATS did not accept connections at {host}:{port} within {timeout}s")


@pytest.fixture(scope="session")
def nkey_seed(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    """Fresh Ed25519 user nkey. Returns (seed_path, pubkey_str)."""
    raw = os.urandom(32)
    seed = nkeys.encode_seed(raw, nkeys.PREFIX_BYTE_USER)  # bytes, starts with b"SU..."
    kp = nkeys.from_seed(seed)
    pubkey = bytes(kp.public_key).decode("ascii")  # str, starts with "U..."
    tmp = tmp_path_factory.mktemp("nkey")
    seed_path = tmp / "nkey.seed"
    seed_path.write_bytes(seed)
    os.chmod(seed_path, 0o600)
    return seed_path, pubkey


@pytest.fixture(scope="session")
def nats_conf(nkey_seed: tuple[Path, str], tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Rendered nats-server.conf with {{PUBKEY}} filled in."""
    _, pubkey = nkey_seed
    template = CONF_TEMPLATE.read_text()
    rendered = template.replace("{{PUBKEY}}", pubkey)
    tmp = tmp_path_factory.mktemp("nats-conf")
    conf_path = tmp / "nats-server.conf"
    conf_path.write_text(rendered)
    return conf_path


@pytest.fixture(scope="session")
def compose_stack(nkey_seed: tuple[Path, str], nats_conf: Path):
    """docker compose up -d; poll localhost:4222; yield env dict; down -v on teardown."""
    if shutil.which("docker") is None:
        pytest.skip("Docker not available — skipping e2e compose stack")

    seed_path, _ = nkey_seed
    env = {
        **os.environ,
        "REPO_ROOT": str(REPO_ROOT),
        "SEED_PATH": str(seed_path),
        "NATS_CONF_PATH": str(nats_conf),
        # Pass host uid/gid so containers can read the 0o600 seed bind-mount.
        "HOST_UID": str(os.getuid()),
        "HOST_GID": str(os.getgid()),
    }

    # -p e2e: pin project name so CI teardown (`docker compose -p e2e ...`)
    # targets the same stack the fixture brought up.
    up = subprocess.run(
        ["docker", "compose", "-p", "e2e", "-f", str(COMPOSE_FILE), "up", "-d"],
        env=env,
        capture_output=True,
        text=True,
    )
    if up.returncode != 0:
        pytest.fail(f"docker compose up failed:\nSTDOUT:\n{up.stdout}\nSTDERR:\n{up.stderr}")

    try:
        _poll_nats("localhost", 4222, READINESS_TIMEOUT)
        yield env
    finally:
        subprocess.run(
            ["docker", "compose", "-p", "e2e", "-f", str(COMPOSE_FILE), "down", "-v"],
            env=env,
            capture_output=True,
            text=True,
        )
