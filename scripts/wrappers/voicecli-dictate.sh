#!/bin/bash
# voicecli-dictate — desktop-shortcut entrypoint for `voicecli dictate nats`.
#
# Designed to be invoked by a compositor `Spawn(...)` action (COSMIC custom
# shortcut, GNOME custom keybinding, etc.) where `PATH` is minimal and no
# interactive shell startup runs. The wrapper:
#   1. extends PATH so notify-send / wl-copy / wtype / xdotool resolve;
#   2. path-repairs a missing/dangling ~/.local/bin/voicecli (local only);
#   3. asks voicecli for the resolved NATS host:port and does a 2-s TCP probe,
#      surfacing a desktop notification if the hub is unreachable instead of
#      blocking on the CLI's 60-s timeout;
#   4. delegates to `voicecli dictate nats`. BLOBSTORE_* / NATS_URL /
#      NATS_NKEY_SEED_PATH / mode are resolved by voicecli itself (env > toml
#      tables `[blobstore]` / `[nats]`).
#
# Overridable via env:
#   VOICECLI_BIN            — voicecli command name or path (default: voicecli)
#   VOICECLI_MODE           — STT mode passed as --mode
#   VOICECLI_REPO           — git checkout for relink/upgrade
#                             (default: install pin → candidate scan)
#   VOICECLI_TRACK_BRANCH   — branch for *upgrade* heal only (default: staging)
#   VOICECLI_AUTO_HEAL=0    — disable path-repair and branch/deps upgrade
#
# Two recovery modes (do not conflate):
#   path-repair (relink) — missing/dangling binary after a repo move.
#     Local only: discover repo, optional `uv sync --extra nats` if .venv
#     binary missing, ln -sf. No dirty check, no git fetch/pull.
#   upgrade — stale branch / missing nats extra / ImportError.
#     Requires clean tree; fetch/checkout/pull + uv sync --extra nats.

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
VOICECLI_TRACK_BRANCH="${VOICECLI_TRACK_BRANCH:-staging}"
VOICECLI_AUTO_HEAL="${VOICECLI_AUTO_HEAL:-1}"
_RELINK_ATTEMPTED=0
_UPGRADE_ATTEMPTED=0

# Install-time pin written by install-shortcut.sh (absolute checkout path).
_REPO_PIN_FILE="${VOICECLI_REPO_PIN:-$HOME/.roxabi/voicecli/repo-path}"
_HEAL_LOG="${VOICECLI_HEAL_LOG:-$HOME/.local/state/voicecli/heal.log}"

_resolve_repo() {
    if [ -n "${VOICECLI_REPO:-}" ]; then
        return 0
    fi
    if [ -f "$_REPO_PIN_FILE" ]; then
        local pinned
        pinned="$(head -n1 "$_REPO_PIN_FILE" 2>/dev/null | tr -d '\r' || true)"
        if [ -n "$pinned" ] && { [ -e "$pinned/.git" ] || [ -d "$pinned" ]; }; then
            VOICECLI_REPO="$pinned"
            return 0
        fi
    fi
    local _candidate
    for _candidate in \
        "$HOME/projects/roxabi/voiceCLI" \
        "$HOME/projects/voiceCLI"; do
        # Worktrees use a .git *file*; plain checkouts use a directory.
        if [ -e "$_candidate/.git" ]; then
            VOICECLI_REPO="$_candidate"
            return 0
        fi
    done
    VOICECLI_REPO="${VOICECLI_REPO:-$HOME/projects/roxabi/voiceCLI}"
}

_resolve_repo

_notify() {
    if command -v notify-send >/dev/null 2>&1; then
        notify-send -u normal -r 2 "VoiceCLI" "$1"
    fi
}

_heal_log() {
    mkdir -p "$(dirname "$_HEAL_LOG")" 2>/dev/null || true
    # Keep log small: last ~200 lines max by rewriting when oversized.
    if [ -f "$_HEAL_LOG" ]; then
        local _sz
        _sz="$(wc -c <"$_HEAL_LOG" 2>/dev/null || echo 0)"
        if [ "${_sz:-0}" -gt 50000 ] 2>/dev/null; then
            tail -n 200 "$_HEAL_LOG" >"${_HEAL_LOG}.tmp" 2>/dev/null \
                && mv "${_HEAL_LOG}.tmp" "$_HEAL_LOG" 2>/dev/null || true
        fi
    fi
    printf '%s %s\n' "$(date -Iseconds 2>/dev/null || date)" "$*" >>"$_HEAL_LOG" 2>/dev/null || true
}

_voicecli_missing() {
    # Broken symlink after a repo move: command -v may still find the path.
    if ! command -v "$VOICECLI_BIN" >/dev/null 2>&1; then
        return 0
    fi
    local resolved
    resolved="$(command -v "$VOICECLI_BIN" 2>/dev/null || true)"
    [ -n "$resolved" ] && [ ! -x "$resolved" ] && return 0
    # Dangling symlink: -L true, target missing (-e false).
    if [ -n "$resolved" ] && [ -L "$resolved" ] && [ ! -e "$resolved" ]; then
        return 0
    fi
    return 1
}

_dictate_nats_unavailable() {
    # Only meaningful when a binary exists; missing binary is a relink problem.
    _voicecli_missing && return 1
    local probe
    probe="$("$VOICECLI_BIN" dictate nats-host 2>&1 >/dev/null || true)"
    grep -qE "No such command 'nats" <<<"$probe"
}

_select_voicecli_bin() {
    if [ -x "$VOICECLI_REPO/.venv/bin/voicecli" ]; then
        mkdir -p "$HOME/.local/bin"
        ln -sf "$VOICECLI_REPO/.venv/bin/voicecli" "$HOME/.local/bin/voicecli"
        VOICECLI_BIN="$VOICECLI_REPO/.venv/bin/voicecli"
        return 0
    fi
    return 1
}

# Path-repair: local only. No dirty check, no git fetch/pull/checkout.
_relink_voicecli() {
    local reason="${1:-chemin cassé}"

    [ "$VOICECLI_AUTO_HEAL" = "1" ] || return 1
    [ "$_RELINK_ATTEMPTED" -eq 0 ] || return 1
    _RELINK_ATTEMPTED=1

    _resolve_repo
    _heal_log "relink start reason=$reason repo=$VOICECLI_REPO"

    if [ ! -e "$VOICECLI_REPO/.git" ] && [ ! -d "$VOICECLI_REPO" ]; then
        _heal_log "relink abort: repo missing ($VOICECLI_REPO)"
        _notify "Relink échoué — dépôt introuvable ($VOICECLI_REPO). Voir heal.log"
        return 1
    fi

    if [ ! -x "$VOICECLI_REPO/.venv/bin/voicecli" ]; then
        if ! command -v uv >/dev/null 2>&1; then
            _heal_log "relink abort: no .venv/bin/voicecli and no uv"
            _notify "Relink échoué — pas de voicecli dans .venv et uv absent. Voir heal.log"
            return 1
        fi
        _notify "Réparation voiceCLI (venv local)…"
        local sync_log
        sync_log="$(mktemp "${TMPDIR:-/tmp}/voicecli-relink.XXXXXX")"
        if ! (
            set -e
            cd "$VOICECLI_REPO"
            uv sync --extra nats
        ) >"$sync_log" 2>&1; then
            _heal_log "relink uv sync failed"
            if [ -s "$sync_log" ]; then
                tail -5 "$sync_log" >>"$_HEAL_LOG" 2>/dev/null || true
                tail -5 "$sync_log" >&2
            fi
            rm -f "$sync_log"
            _notify "Relink échoué — uv sync a échoué. Voir ~/.local/state/voicecli/heal.log"
            return 1
        fi
        rm -f "$sync_log"
    fi

    if ! _select_voicecli_bin; then
        _heal_log "relink abort: binary still missing after sync"
        _notify "Relink échoué — .venv/bin/voicecli toujours absent. Voir heal.log"
        return 1
    fi

    if ! [ -x "$VOICECLI_BIN" ]; then
        _heal_log "relink abort: VOICECLI_BIN not executable ($VOICECLI_BIN)"
        _notify "Relink échoué — binaire non exécutable. Voir heal.log"
        return 1
    fi

    _heal_log "relink ok bin=$VOICECLI_BIN"
    _notify "Lien voicecli réparé — nouvel essai"
    return 0
}

# Branch/deps upgrade: clean tree + network + uv. Conservative.
_upgrade_voicecli() {
    local reason="$1"
    local heal_log

    [ "$VOICECLI_AUTO_HEAL" = "1" ] || return 1
    [ "$_UPGRADE_ATTEMPTED" -eq 0 ] || return 1
    _UPGRADE_ATTEMPTED=1

    _resolve_repo
    _heal_log "upgrade start reason=$reason repo=$VOICECLI_REPO branch=$VOICECLI_TRACK_BRANCH"

    if [ ! -e "$VOICECLI_REPO/.git" ]; then
        _heal_log "upgrade abort: no .git at $VOICECLI_REPO"
        _notify "Auto-heal échoué — pas de dépôt git. Voir heal.log"
        return 1
    fi
    if ! command -v uv >/dev/null 2>&1; then
        _heal_log "upgrade abort: uv missing"
        _notify "Auto-heal échoué — uv introuvable. Voir heal.log"
        return 1
    fi
    if [ -n "$(git -C "$VOICECLI_REPO" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
        _heal_log "upgrade abort: dirty worktree"
        _notify "Auto-heal bloqué — voiceCLI a des changements locaux (commit/stash)"
        return 1
    fi

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
        _heal_log "upgrade failed (git/uv)"
        if [ -s "$heal_log" ]; then
            tail -20 "$heal_log" >>"$_HEAL_LOG" 2>/dev/null || true
            tail -5 "$heal_log" >&2
        fi
        rm -f "$heal_log"
        _notify "Auto-heal échoué — voir ~/.local/state/voicecli/heal.log"
        return 1
    fi
    rm -f "$heal_log"

    if ! _select_voicecli_bin; then
        _heal_log "upgrade abort: binary missing after sync"
        _notify "Auto-heal échoué — binaire absent après sync. Voir heal.log"
        return 1
    fi
    if ! [ -x "$VOICECLI_BIN" ]; then
        _heal_log "upgrade abort: binary not executable"
        _notify "Auto-heal échoué — binaire non exécutable. Voir heal.log"
        return 1
    fi

    _heal_log "upgrade ok bin=$VOICECLI_BIN"
    _notify "voiceCLI à jour ($VOICECLI_TRACK_BRANCH) — nouvel essai"
    return 0
}

# Back-compat name used by older docs / tests referring to upgrade heal.
_heal_voicecli() {
    _upgrade_voicecli "$@"
}

_cli_needs_relink() {
    local log="$1"
    grep -qE "command not found|cannot execute" "$log" 2>/dev/null
}

_cli_needs_upgrade() {
    local log="$1"
    # Deliberately narrow: do NOT match bare "No such file or directory"
    # (unrelated path errors would force staging + nats-only sync).
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

# Pre-flight: path-repair first (local), then upgrade only for missing nats cmd.
if _voicecli_missing; then
    _relink_voicecli "voicecli introuvable (chemin cassé ?)" || true
fi
if ! _voicecli_missing && _dictate_nats_unavailable; then
    _upgrade_voicecli "commande dictate nats absente" || true
fi

# Pre-flight: 2-second TCP probe so the user gets immediate feedback when
# the hub is unreachable (offline, Tailscale down, hub off). Skipped when
# the URL cannot be resolved — voicecli will print a clearer error than us.
if ! _voicecli_missing && command -v nc >/dev/null 2>&1; then
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

if [ "$EXIT_CODE" -ne 0 ]; then
    if _cli_needs_relink "$CLI_LOG" && _relink_voicecli "binaire manquant après échec"; then
        _run_dictate
        EXIT_CODE=$?
    elif _cli_needs_upgrade "$CLI_LOG" && _upgrade_voicecli "dépendances ou branche obsolètes"; then
        _run_dictate
        EXIT_CODE=$?
    fi
fi

# Exit 69 = TCP probe failed (hub unreachable, already notified above).
if [ "$EXIT_CODE" -ne 0 ] && [ "$EXIT_CODE" -ne 69 ] && command -v notify-send >/dev/null 2>&1; then
    RECORDER_LOG="$HOME/.local/state/voicecli/recorder.log"
    _has_import_error() {
        grep -qE "ImportError|ModuleNotFoundError" "$1" 2>/dev/null
    }

    if _has_import_error "$CLI_LOG" || { [ -f "$RECORDER_LOG" ] && tail -n 50 "$RECORDER_LOG" | grep -qE "ImportError|ModuleNotFoundError"; }; then
        notify-send -u normal -r 2 "VoiceCLI" \
            "Dictate failed: deps NATS manquantes. Auto-heal a échoué — cd ~/projects/roxabi/voiceCLI && uv sync --extra nats"
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
