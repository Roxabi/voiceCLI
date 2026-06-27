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
#   VOICECLI_BIN            — voicecli command name or path (default: voicecli, resolved via PATH)
#   VOICECLI_MODE           — STT mode passed as --mode (default: unset, uses voicecli's default)
#   VOICECLI_REPO           — voiceCLI git checkout used for auto-heal (default: ~/projects/voiceCLI)
#   VOICECLI_TRACK_BRANCH   — branch to checkout on heal (default: staging)
#   VOICECLI_AUTO_HEAL=0    — disable checkout/pull/uv sync recovery
#
# Auto-heal (once per invocation): when `dictate nats` is missing (stale main
# checkout) or NATS extras are stale (ImportError), the wrapper runs
# ``git checkout <track> && uv sync --extra nats`` then retries.

set -u

# Ensure GUI helpers (notify-send, wl-copy, wtype) AND the voicecli command
# itself resolve under minimal Spawn() PATHs. ~/.local/bin is the standard
# location for `uv tool install` / pip --user / our own symlink.
export PATH="/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin:${PATH:-}"

# Blobstore credentials — factory SSoT on M₁/M₂ when present; legacy env fallback.
FACTORY_BLOBSTORE_TOK="$HOME/.roxabi/factory/blobstore.tok"
BLOBSTORE_ENV="$HOME/.roxabi/voicecli/env/blobstore.env"
if [ -f "$FACTORY_BLOBSTORE_TOK" ]; then
    export BLOBSTORE_BEARER_TOKEN_PATH="$FACTORY_BLOBSTORE_TOK"
elif [ -f "$BLOBSTORE_ENV" ]; then
    while IFS='=' read -r key value; do
        case "$key" in
            BLOBSTORE_BACKEND|BLOBSTORE_URL|BLOBSTORE_BEARER_TOKEN)
                export "$key=$value"
                ;;
        esac
    done < <(grep -E '^(BLOBSTORE_BACKEND|BLOBSTORE_URL|BLOBSTORE_BEARER_TOKEN)=' "$BLOBSTORE_ENV")
fi

VOICECLI_BIN="${VOICECLI_BIN:-voicecli}"
VOICECLI_REPO="${VOICECLI_REPO:-$HOME/projects/voiceCLI}"
VOICECLI_TRACK_BRANCH="${VOICECLI_TRACK_BRANCH:-staging}"
VOICECLI_AUTO_HEAL="${VOICECLI_AUTO_HEAL:-1}"
_HEAL_ATTEMPTED=0

_notify() {
    if command -v notify-send >/dev/null 2>&1; then
        notify-send -u normal -r 2 "VoiceCLI" "$1"
    fi
}

_dictate_nats_unavailable() {
    local probe
    probe="$("$VOICECLI_BIN" dictate nats-host 2>&1 >/dev/null || true)"
    grep -qE "No such command 'nats" <<<"$probe"
}

_log_heal_failure() {
    local log="$1"
    if [ -s "$log" ]; then
        tail -5 "$log" >&2
    fi
}

_heal_voicecli() {
    local reason="$1"
    local heal_log

    [ "$VOICECLI_AUTO_HEAL" = "1" ] || return 1
    [ "$_HEAL_ATTEMPTED" -eq 0 ] || return 1
    _HEAL_ATTEMPTED=1

    [ -d "$VOICECLI_REPO/.git" ] || return 1
    command -v uv >/dev/null 2>&1 || return 1

    _notify "Mise à jour voiceCLI ($reason)…"

    heal_log="$(mktemp "${TMPDIR:-/tmp}/voicecli-heal.XXXXXX")"
    if ! (
        set -e
        cd "$VOICECLI_REPO"
        git fetch origin "$VOICECLI_TRACK_BRANCH"
        git checkout "$VOICECLI_TRACK_BRANCH"
        git pull --ff-only origin "$VOICECLI_TRACK_BRANCH"
        uv sync --extra nats
    ) >"$heal_log" 2>&1; then
        _notify "Auto-heal échoué — voir journal"
        _log_heal_failure "$heal_log"
        rm -f "$heal_log"
        return 1
    fi
    rm -f "$heal_log"

    if [ -x "$VOICECLI_REPO/.venv/bin/voicecli" ]; then
        ln -sf "$VOICECLI_REPO/.venv/bin/voicecli" "$HOME/.local/bin/voicecli"
        VOICECLI_BIN="$VOICECLI_REPO/.venv/bin/voicecli"
    fi

    _notify "voiceCLI à jour ($VOICECLI_TRACK_BRANCH) — nouvel essai"
    return 0
}

_cli_needs_heal() {
    local log="$1"
    grep -qE "No such command 'nats|ImportError|ModuleNotFoundError" "$log" 2>/dev/null
}

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

# Pre-flight: heal stale checkout before probing NATS or invoking dictate.
if command -v "$VOICECLI_BIN" >/dev/null 2>&1 && _dictate_nats_unavailable; then
    _heal_voicecli "commande dictate nats absente" || true
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

CLI_LOG="$(mktemp "${TMPDIR:-/tmp}/voicecli-dictate.XXXXXX")"
trap 'rm -f "$CLI_LOG"' EXIT

_run_dictate() {
    local run_cmd=( "$VOICECLI_BIN" dictate nats )
    if [ -n "${VOICECLI_MODE:-}" ]; then
        run_cmd+=( --mode "$VOICECLI_MODE" )
    fi
    "${run_cmd[@]}" >"$CLI_LOG" 2>&1
}

_run_dictate
EXIT_CODE=$?

if [ "$EXIT_CODE" -ne 0 ] && _cli_needs_heal "$CLI_LOG" && _heal_voicecli "dépendances ou branche obsolètes"; then
    _run_dictate
    EXIT_CODE=$?
fi

# Exit 69 = TCP probe failed (hub unreachable, already notified above).
if [ "$EXIT_CODE" -ne 0 ] && [ "$EXIT_CODE" -ne 69 ] && command -v notify-send >/dev/null 2>&1; then
    RECORDER_LOG="$HOME/.local/state/voicecli/recorder.log"
    _has_import_error() {
        grep -qE "ImportError|ModuleNotFoundError" "$1" 2>/dev/null
    }

    if _has_import_error "$CLI_LOG" || { [ -f "$RECORDER_LOG" ] && tail -n 50 "$RECORDER_LOG" | grep -qE "ImportError|ModuleNotFoundError"; }; then
        notify-send -u normal -r 2 "VoiceCLI" \
            "Dictate failed: deps NATS manquantes. Auto-heal a échoué — cd ~/projects/voiceCLI && uv sync --extra nats"
    elif grep -qE "No such command 'nats" "$CLI_LOG" 2>/dev/null; then
        notify-send -u normal -r 2 "VoiceCLI" \
            "Dictate failed: branche voiceCLI trop vieille. Auto-heal a échoué — git checkout staging && uv sync --extra nats"
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
