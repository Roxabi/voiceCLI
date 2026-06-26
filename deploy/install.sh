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

# ── Blobstore bearer (factory SSoT) ───────────────────────────────────────────
factory_blobstore_tok="${HOME}/.roxabi/factory/blobstore.tok"
legacy_blobstore_env="${HOME}/.roxabi/voicecli/env/blobstore.env"
if [[ ! -f "$factory_blobstore_tok" ]]; then
    echo "ERROR: ${factory_blobstore_tok} not found." >&2
    echo "  Run roxabi-factory deploy/install.sh on this host first (generates blobstore.tok)." >&2
    exit 1
fi
if [[ -f "$legacy_blobstore_env" ]]; then
    echo "WARN: legacy ${legacy_blobstore_env} is unused — Quadlet bind-mounts ${factory_blobstore_tok}" >&2
    echo "  Safe to remove after voicecli-stt/tts restart cleanly." >&2
fi
if [[ "$DRY_RUN" -eq 0 ]]; then
    if [[ ! -s "$factory_blobstore_tok" ]]; then
        echo "ERROR: ${factory_blobstore_tok} is empty — regenerate via factory install.sh" >&2
        exit 1
    fi
    echo "OK: factory blobstore token present at ${factory_blobstore_tok}"
fi

for src in "${SCRIPT_DIR}"/quadlet/*.container; do
    unit="$(basename "$src")"
    dst="${QUADLET_DIR}/${unit}"
    if [[ ! -f "$src" ]]; then
        echo "ERROR: no .container units found in ${SCRIPT_DIR}/quadlet/" >&2
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
