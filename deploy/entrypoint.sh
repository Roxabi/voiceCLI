#!/bin/bash
set -e

MODE="${1:-tts}"

case "$MODE" in
    tts|stt)
        exec voicecli nats-serve "$MODE"
        ;;
    *)
        echo "Unknown mode: $MODE" >&2
        exit 1
        ;;
esac
