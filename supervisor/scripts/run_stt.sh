#!/usr/bin/env bash
# Wrapper for voicecli_stt daemon — sources .env before launching.
# supervisor conf points to this script so secrets never live in conf files.
set -a
[ -f "$HOME/projects/voiceCLI/.env" ] && source "$HOME/projects/voiceCLI/.env"
set +a
source "$(dirname "$0")/ensure-pulse.sh"
exec voicecli stt-serve
