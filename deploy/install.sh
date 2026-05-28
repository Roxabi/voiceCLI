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
#   - nkeys at ~/.roxabi/voicecli/nkeys/voice-{stt,tts}.seed (0600)
#   - podman, systemctl --user

set -euo pipefail

NKEYS_DIR="${HOME}/.roxabi/voicecli/nkeys"
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
run mkdir -p "${HOME}/.roxabi/voicecli/env"
run mkdir -p "${HOME}/.roxabi/voicecli/env"

# ── Env stubs (S6) ────────────────────────────────────────────────────────────
for role in stt tts; do
    env_file="${HOME}/.roxabi/voicecli/env/${role}.env"
    if [[ ! -f "$env_file" ]]; then
        run bash -c "cat > '${env_file}'" <<'EOF'
# voiceCLI satellite env (S6)
HF_HOME=/home/voicecli/.cache/huggingface
EOF
        echo "Created ${env_file}"
    else
        echo "Keep ${env_file} (exists)"
    fi
done

# ── Blobstore env stub ────────────────────────────────────────────────────────
blobstore_env="${HOME}/.roxabi/voicecli/env/blobstore.env"
if [[ ! -f "$blobstore_env" ]]; then
    run bash -c "cat > '${blobstore_env}'" <<'EOF'
# voiceCLI blobstore credentials (ADR-068)
# Fill in the bearer token issued by the lyra blobstore service.
BLOBSTORE_BEARER_TOKEN=
EOF
    run chmod 600 "$blobstore_env"
    echo "Created ${blobstore_env} (mode 600)"
else
    perms=$(stat -c '%a' "$blobstore_env")
    if [[ "$perms" != "600" ]]; then
        echo "ERROR: $blobstore_env has permissions $perms (expected 600 — bearer token is a credential)" >&2
        echo "  Fix: chmod 600 $blobstore_env" >&2
        exit 1
    fi
    echo "Keep ${blobstore_env} (exists, mode 600)"
fi

# Validate that the bearer token has been filled in before deploy. An empty
# value would cause every get_blobstore() call to raise BlobstoreConfigError
# at satellite startup and enter the systemd RestartSec=10 loop silently.
if [[ "$DRY_RUN" -eq 0 ]]; then
    token_value=$(grep -E '^BLOBSTORE_BEARER_TOKEN=' "$blobstore_env" | head -1 | cut -d= -f2-)
    if [[ -z "$token_value" ]]; then
        echo "ERROR: BLOBSTORE_BEARER_TOKEN is empty in $blobstore_env" >&2
        echo "  Fill in the token issued by the lyra blobstore service, then re-run." >&2
        exit 1
    fi
fi

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
