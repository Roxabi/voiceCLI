#!/usr/bin/env bash
set -euo pipefail
umask 0077

OLD_DIR="${HOME}/.voicecli"
NEW_DIR="${HOME}/.roxabi/voicecli"

echo "==> voiceCLI data directory migration"
echo "    From: ${OLD_DIR}"
echo "    To:   ${NEW_DIR}"

# Check for broken symlinks too
if [[ ! -e "${OLD_DIR}" && ! -L "${OLD_DIR}" ]]; then
    echo "ERROR: ${OLD_DIR} does not exist. Nothing to migrate."
    exit 1
fi

# Guard against OLD_DIR being a symlink (would move the symlink node, not the data)
if [[ -L "${OLD_DIR}" ]]; then
    echo "ERROR: ${OLD_DIR} is a symlink. Dereference it first (e.g., cp -aL). Aborting."
    exit 1
fi

# Verify OLD_DIR contains expected voiceCLI data
if [[ ! -f "${OLD_DIR}/voicecli.toml" && ! -d "${OLD_DIR}/TTS" && ! -d "${OLD_DIR}/STT" && ! -d "${OLD_DIR}/nkeys" ]]; then
    echo "ERROR: ${OLD_DIR} does not contain expected voiceCLI data (voicecli.toml, TTS/, STT/, or nkeys/). Aborting."
    exit 1
fi

if [[ -e "${NEW_DIR}" ]]; then
    echo "ERROR: ${NEW_DIR} already exists. Aborting to avoid overwriting."
    exit 1
fi

# Preflight: warn if services are running
if systemctl --user is-active voicecli-tts voicecli-stt &>/dev/null || pgrep -f 'voicecli (serve|stt-serve)' &>/dev/null; then
    echo "WARNING: voiceCLI services appear to be running. Stop them before migration to avoid data loss."
    echo "         systemctl --user stop voicecli-tts voicecli-stt"
    echo "         pkill -f 'voicecli serve'"
    echo "         pkill -f 'voicecli stt-serve'"
    read -rp "Continue anyway? [y/N] " ans
    [[ "${ans}" =~ ^[Yy]$ ]] || exit 1
fi

# Ensure parent directory exists
mkdir -p "$(dirname "${NEW_DIR}")"

# Move data
if [[ "${OLD_DIR}" -ef "${NEW_DIR}" ]]; then
    echo "ERROR: ${OLD_DIR} and ${NEW_DIR} are the same filesystem entry. Aborting."
    exit 1
fi

if command -v rsync &>/dev/null; then
    echo "==> Moving data to ${NEW_DIR}..."
    rsync -a "${OLD_DIR}/" "${NEW_DIR}/"
    rm -rf --one-file-system "${OLD_DIR}"
else
    echo "==> Moving data to ${NEW_DIR}..."
    cp -a "${OLD_DIR}/" "${NEW_DIR}/"
    rm -rf --one-file-system "${OLD_DIR}"
fi

# Restrict permissions on migrated data (find avoids following symlinks)
find "${NEW_DIR}" -xdev \( -type f -o -type d \) -exec chmod go-rwx {} + || true

# Create backward-compatibility symlink
echo "==> Creating symlink ${OLD_DIR} -> ${NEW_DIR}..."
ln -s "${NEW_DIR}" "${OLD_DIR}"

echo ""
echo "==> Migration complete."
echo ""
echo "Next steps:"
echo "  1. Update any hardcoded paths in your shell profile / scripts."
echo "  2. Restart voiceCLI services:"
echo "       systemctl --user restart voicecli-tts voicecli-stt"
echo "  3. If running native socket daemons, stop and restart them:"
echo "       pkill -f 'voicecli serve' && voicecli serve &"
echo "       pkill -f 'voicecli stt-serve' && voicecli stt-serve &"
echo "  4. Once verified, remove the symlink and update all callers:"
echo "       rm \"${OLD_DIR}\""
