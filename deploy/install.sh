#!/usr/bin/env bash
# deploy/install.sh — idempotent Quadlet installer for voiceCLI
#
# Usage:
#   ./deploy/install.sh [--dry-run] [--secrets-only] [--force]
#
# Flags:
#   --dry-run       Print actions without executing them
#   --secrets-only  Only (re)create Podman secrets; skip Quadlet copy + daemon-reload
#   --force         Re-create secrets even if they already exist (implies --replace)
#
# Prerequisites:
#   - nkeys at ~/.voicecli/nkeys/voice-{stt,tts}.seed (0600)
#   - podman, systemctl --user

set -euo pipefail

NKEYS_DIR="${HOME}/.voicecli/nkeys"
QUADLET_DIR="${HOME}/.config/containers/systemd"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DRY_RUN=0
SECRETS_ONLY=0
FORCE=0

for arg in "$@"; do
    case "$arg" in
        --dry-run)      DRY_RUN=1 ;;
        --secrets-only) SECRETS_ONLY=1 ;;
        --force)        FORCE=1 ;;
        *) echo "Unknown flag: $arg" >&2; exit 1 ;;
    esac
done

run() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "[dry-run] $*"
    else
        "$@"
    fi
}

# ── 1. Verify nkeys ───────────────────────────────────────────────────────────

for role in stt tts; do
    seed="${NKEYS_DIR}/voice-${role}.seed"
    if [[ ! -f "$seed" ]]; then
        echo "ERROR: nkey seed not found: $seed" >&2
        echo "  Run the seed-relocation runbook first: docs/QUADLET-DEPLOYMENT.md" >&2
        exit 1
    fi
    perms=$(stat -c '%a' "$seed")
    if [[ "$perms" != "600" ]]; then
        echo "ERROR: $seed has permissions $perms (expected 600)" >&2
        exit 1
    fi
done

# ── 2. Podman secrets ─────────────────────────────────────────────────────────

for role in stt tts; do
    secret_name="voicecli-nats-${role}"
    seed="${NKEYS_DIR}/voice-${role}.seed"

    if podman secret inspect "$secret_name" &>/dev/null && [[ "$FORCE" -eq 0 ]]; then
        echo "Secret $secret_name already exists (skip; use --force to replace)"
    else
        run podman secret create --replace "$secret_name" "$seed"
        echo "Secret $secret_name created."
    fi
done

if [[ "$SECRETS_ONLY" -eq 1 ]]; then
    echo "Done (--secrets-only)."
    exit 0
fi

# ── 3. Copy Quadlet units ─────────────────────────────────────────────────────

run mkdir -p "$QUADLET_DIR"
run mkdir -p "${HOME}/.cache/huggingface" "${HOME}/.cache/voicecli"

for unit in voicecli-stt.container voicecli-tts.container; do
    src="${SCRIPT_DIR}/quadlet/${unit}"
    dst="${QUADLET_DIR}/${unit}"
    if [[ ! -f "$src" ]]; then
        echo "ERROR: Quadlet unit not found: $src" >&2
        exit 1
    fi
    run cp "$src" "$dst"
    echo "Installed $dst"
done

# ── 4. daemon-reload ─────────────────────────────────────────────────────────

run systemctl --user daemon-reload
echo "daemon-reload done."
echo ""
echo "Next: systemctl --user start voicecli-tts voicecli-stt"
echo "Verify: systemctl --user status voicecli-{tts,stt}"
