#!/usr/bin/env bash
# Enforce ADR-047 Rule 3: lyra.* subject literals only in designated adapter modules
set -euo pipefail

# Designated adapter modules (actual subject string usage)
# cli.py: docstrings only, not subject literals
# transcribe_client.py: one-shot NATS STT adapter for `voicecli dictate nats`
ALLOWLIST="src/voicecli/adapters/nats/transcribe_adapter.py src/voicecli/adapters/nats/synthesize_adapter.py src/voicecli/cli/nats.py src/voicecli/adapters/nats/transcribe_client.py"

# Find files with lyra. literals, excluding allowlisted paths
VIOLATORS=$(grep -rln "lyra\." src/ --include='*.py' 2>/dev/null | grep -vE "^($(echo "$ALLOWLIST" | tr ' ' '|'))$" || true)

if [ -n "$VIOLATORS" ]; then
    echo "ERROR: lyra.* subject literal outside designated adapter module:"
    echo "$VIOLATORS"
    echo ""
    echo "Add to allowlist in scripts/check-lyra-literals.sh if this is a new designated module (requires ADR)."
    exit 1
fi

echo "OK: No lyra.* subject literals outside designated adapter modules."
exit 0
