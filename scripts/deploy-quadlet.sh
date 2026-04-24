#!/bin/bash
# Deploy script for voiceCLI production — Quadlet/podman path.
# Sources the shared deploy-lib.sh from Lyra (ADR-055 D5 / SSoT) and runs
# the standard pull → test → build → restart pipeline with voiceCLI-specific vars.
#
# Installation: `make quadlet-install-deploy-lib` (in lyra repo) installs the
# library to ~/.local/lib/roxabi/ for consumers. See docs/DEPLOYMENT-quadlet.md.
set -euo pipefail
umask 0077
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

export PATH="$HOME/.local/bin:$PATH"
source "$HOME/.local/bin/env" 2>/dev/null || true  # uv

# ── Project variables ─────────────────────────────────────────────────────────

PROJECT="voicecli"
PROJECT_DIR="$HOME/projects/voiceCLI"
PROJECT_BRANCH="staging"
IMAGE="ghcr.io/roxabi/voicecli:latest"
DOCKERFILE="Dockerfile"
# voiceCLI has no hub — NATS is the hub; workers subscribe to the queue group.
HUB_SERVICE=""
ADAPTER_SERVICES="voicecli-stt voicecli-tts"
ENV_FILES_DIR="$HOME/.voicecli/env"
ENV_FILES=""
LOG_FILE="$HOME/.local/state/voicecli/logs/deploy.log"
FAIL_FILE="$HOME/.local/state/voicecli/deploy_failed_shas.txt"
PROJECT_TEST_CMD="uv run pytest --tb=short -q"

# No extra repos — voiceCLI is a leaf project.
EXTRA_REPOS=""

# ── Source library and run ────────────────────────────────────────────────────
# Library ships from Lyra (single source of truth per ADR-055 D5).

LIB_PATH="$HOME/.local/lib/roxabi/deploy-lib.sh"
[[ -f "$LIB_PATH" ]] || {
    echo "ERROR: deploy-lib.sh not found at $LIB_PATH" >&2
    echo "Install it from Lyra: cd ~/projects/lyra && make quadlet-install-deploy-lib" >&2
    exit 1
}
source "$LIB_PATH"
run_deploy "$@"
