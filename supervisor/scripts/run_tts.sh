#!/usr/bin/env bash
# Wrapper for voicecli_tts daemon — sources .env before launching.
# supervisor conf points to this script so secrets never live in conf files.
set -a
[ -f "$HOME/projects/voiceCLI/.env" ] && source "$HOME/projects/voiceCLI/.env"
set +a
exec "$HOME/.local/bin/voicecli" serve --engine qwen-fast
