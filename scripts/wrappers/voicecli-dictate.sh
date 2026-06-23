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
#   3. delegates to `voicecli dictate nats`. BLOBSTORE_* / NATS_URL /
#      NATS_NKEY_SEED_PATH / mode are resolved by voicecli itself (env > toml
#      tables `[blobstore]` / `[nats]`).
#
# Overridable via env:
#   VOICECLI_BIN   — voicecli command name or path (default: voicecli, resolved via PATH)
#   VOICECLI_MODE  — STT mode passed as --mode (default: unset, uses voicecli's default)

set -u

# Ensure GUI helpers (notify-send, wl-copy, wtype) AND the voicecli command
# itself resolve under minimal Spawn() PATHs. ~/.local/bin is the standard
# location for `uv tool install` / pip --user / our own symlink.
export PATH="/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin:${PATH:-}"

# Blobstore env file (created by deploy/install.sh). Sourced here because
# compositor Spawn() actions do not run an interactive shell startup.
BLOBSTORE_ENV="$HOME/.roxabi/voicecli/env/blobstore.env"
if [ -f "$BLOBSTORE_ENV" ]; then
    # set -u safe: only export vars that are actually set in the file
    while IFS='=' read -r key value; do
        case "$key" in
            BLOBSTORE_BACKEND|BLOBSTORE_URL|BLOBSTORE_BEARER_TOKEN)
                export "$key=$value"
                ;;
        esac
    done < <(grep -E '^(BLOBSTORE_BACKEND|BLOBSTORE_URL|BLOBSTORE_BEARER_TOKEN)=' "$BLOBSTORE_ENV")
fi

VOICECLI_BIN="${VOICECLI_BIN:-voicecli}"

# Orphan-recorder guard: if a previous --run-recorder process is still alive
# but the toggle state file is gone (or points at a stale PID), kill it before
# delegating. Otherwise `voicecli dictate nats` would see "not recording" and
# spawn a second recorder on top of the first.
STATE_FILE="$HOME/.local/share/voicecli/nats-recording.json"
if command -v pgrep >/dev/null 2>&1; then
    orphan_pids="$(pgrep -u "$USER" -f 'voicecli\.ui\.nats_mic_recorder .*--run-recorder' || true)"
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

CLI_LOG="$(mktemp "${TMPDIR:-/tmp}/voicecli-dictate.XXXXXX")"
trap 'rm -f "$CLI_LOG"' EXIT

"${cmd[@]}" >"$CLI_LOG" 2>&1
EXIT_CODE=$?

# Exit 69 = TCP probe failed (hub unreachable, already notified above).
if [ "$EXIT_CODE" -ne 0 ] && [ "$EXIT_CODE" -ne 69 ] && command -v notify-send >/dev/null 2>&1; then
    RECORDER_LOG="$HOME/.local/state/voicecli/recorder.log"
    _has_import_error() {
        grep -qE "ImportError|ModuleNotFoundError" "$1" 2>/dev/null
    }

    if _has_import_error "$CLI_LOG" || { [ -f "$RECORDER_LOG" ] && tail -n 50 "$RECORDER_LOG" | grep -qE "ImportError|ModuleNotFoundError"; }; then
        notify-send -u normal -r 2 "VoiceCLI" \
            "Dictate failed: stale .venv. Run: cd ~/projects/voiceCLI && uv sync --extra nats"
    else
        err_line=""
        if [ -s "$CLI_LOG" ]; then
            err_line="$(grep -E '^[A-Z][A-Za-z]+Error:' "$CLI_LOG" 2>/dev/null | tail -1)"
            if [ -z "$err_line" ]; then
                err_line="$(grep -oE '[A-Z][A-Za-z]+Error: [^│]+' "$CLI_LOG" 2>/dev/null | tail -1 | sed 's/[[:space:]]*$//')"
            fi
            if [ -z "$err_line" ]; then
                err_line="$(grep -viE '^(╭|│|╰|─|Traceback|File "|During handling)' "$CLI_LOG" 2>/dev/null | grep -v '^[[:space:]]*$' | tail -1 | sed 's/^[[:space:]]*//')"
            fi
        fi
        if [ -n "$err_line" ]; then
            # notify-send body length is limited; keep the bubble readable.
            err_line="${err_line:0:240}"
            notify-send -u normal -r 2 "VoiceCLI" "Dictate failed: $err_line"
        else
            notify-send -u normal -r 2 "VoiceCLI" "Dictate failed (exit $EXIT_CODE)"
        fi
    fi
fi

exit "$EXIT_CODE"
