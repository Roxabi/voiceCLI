#!/usr/bin/env bash
set -euo pipefail
umask 0077

OLD_DIR="${HOME}/.voicecli"
NEW_DIR="${HOME}/.roxabi/voicecli"

echo "==> voiceCLI data directory migration"
echo "    From: ${OLD_DIR}"
echo "    To:   ${NEW_DIR}"

if [[ ! -d "${OLD_DIR}" ]]; then
    echo "ERROR: ${OLD_DIR} does not exist. Nothing to migrate."
    exit 1
fi

if [[ -e "${NEW_DIR}" ]]; then
    echo "ERROR: ${NEW_DIR} already exists. Aborting to avoid overwriting."
    exit 1
fi

# Ensure parent directory exists
mkdir -p "$(dirname "${NEW_DIR}")"

# Move data
echo "==> Moving data to ${NEW_DIR}..."
if command -v rsync &>/dev/null; then
    rsync -a "${OLD_DIR}/" "${NEW_DIR}/"
    rm -rf "${OLD_DIR}"
else
    mv "${OLD_DIR}" "${NEW_DIR}"
fi

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
echo "       rm ${OLD_DIR}"
