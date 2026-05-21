#!/bin/bash
# voicecli-dictate — desktop-shortcut entrypoint for `voicecli dictate nats`.
#
# Designed to be invoked by a compositor `Spawn(...)` action (COSMIC custom
# shortcut, GNOME custom keybinding, etc.) where `PATH` is minimal and no
# interactive shell startup runs. The wrapper:
#   1. extends PATH so notify-send / wl-copy / wtype / xdotool resolve;
#   2. asks voicecli for the resolved NATS host:port and does a 2-s TCP probe,
#      surfacing a desktop notification if the hub is unreachable instead of
#      blocking on the CLI's 60-s timeout;
#   3. delegates to `voicecli dictate nats`. NATS_URL / NATS_NKEY_SEED_PATH /
#      mode are resolved by voicecli itself (env > `[nats]` table in
#      voicecli.toml).
#
# Overridable via env:
#   VOICECLI_BIN   — voicecli command name or path (default: voicecli, resolved via PATH)
#   VOICECLI_MODE  — STT mode passed as --mode (default: unset, uses voicecli's default)

set -u

# Ensure GUI helpers (notify-send, wl-copy, wtype) AND the voicecli command
# itself resolve under minimal Spawn() PATHs. ~/.local/bin is the standard
# location for `uv tool install` / pip --user / our own symlink.
export PATH="/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin:${PATH:-}"

VOICECLI_BIN="${VOICECLI_BIN:-voicecli}"

# Orphan-recorder guard: if a previous --run-recorder process is still alive
# but the toggle state file is gone (or points at a stale PID), kill it before
# delegating. Otherwise `voicecli dictate nats` would see "not recording" and
# spawn a second recorder on top of the first.
STATE_FILE="$HOME/.local/share/voicecli/nats-recording.json"
if command -v pgrep >/dev/null 2>&1; then
    orphan_pids="$(pgrep -u "$USER" -f 'voicecli\.nats_recorder .*--run-recorder' || true)"
    if [ -n "$orphan_pids" ]; then
        tracked_pid=""
        if [ -f "$STATE_FILE" ] && command -v python3 >/dev/null 2>&1; then
            tracked_pid="$(python3 -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get("pid",""))
except Exception: pass' "$STATE_FILE" 2>/dev/null || true)"
        fi
        for pid in $orphan_pids; do
            [ "$pid" = "$tracked_pid" ] && continue
            kill "$pid" 2>/dev/null || true
        done
        if [ -z "$tracked_pid" ] || ! kill -0 "$tracked_pid" 2>/dev/null; then
            rm -f "$STATE_FILE" \
                  "$HOME/.local/share/voicecli/nats-recording.wav.partial"
            if command -v notify-send >/dev/null 2>&1; then
                notify-send -u low "VoiceCLI" "Cleaned up orphan recorder"
            fi
        fi
    fi
fi

# Pre-flight: 2-second TCP probe so the user gets immediate feedback when
# the hub is unreachable (offline, Tailscale down, hub off). Skipped when
# the URL cannot be resolved — voicecli will print a clearer error than us.
if command -v "$VOICECLI_BIN" >/dev/null 2>&1 && command -v nc >/dev/null 2>&1; then
    host_port="$("$VOICECLI_BIN" dictate nats-host 2>/dev/null || true)"
    if [ -n "$host_port" ]; then
        host="${host_port%:*}"
        port="${host_port##*:}"
        if ! nc -z -w2 "$host" "$port" 2>/dev/null; then
            if command -v notify-send >/dev/null 2>&1; then
                notify-send -u critical -r 1 "VoiceCLI" "Hub injoignable: $host_port"
            fi
            exit 69 # EX_UNAVAILABLE
        fi
    fi
fi

cmd=( "$VOICECLI_BIN" dictate nats )
if [ -n "${VOICECLI_MODE:-}" ]; then
    cmd+=( --mode "$VOICECLI_MODE" )
fi
"${cmd[@]}"
EXIT_CODE=$?

if [ "$EXIT_CODE" -ne 0 ]; then
    LOG="$HOME/.local/state/voicecli/recorder.log"
    if [ -f "$LOG" ] && command -v notify-send >/dev/null 2>&1; then
        if tail -n 50 "$LOG" | grep -q "ImportError"; then
            notify-send -u critical "voicecli-dictate" \
                "Recorder failed: stale .venv. Run: cd ~/projects/voiceCLI && uv sync --extra nats"
        fi
    fi
fi

exit "$EXIT_CODE"
