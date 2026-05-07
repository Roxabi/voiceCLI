#!/bin/bash
# install-shortcut — provision a Linux desktop shortcut for voicecli dictate.
#
# Idempotent. Detects the desktop environment (COSMIC / GNOME), checks Wayland
# dictation deps, installs the wrapper to ~/.local/bin, and binds a keyboard
# shortcut (default Ctrl+Space) to it.
#
# Usage:
#   scripts/install-shortcut.sh [--de cosmic|gnome] [--key '<Ctrl>space'] \
#                               [--bin-dir PATH] [--check-only] [--quiet]
#
# Configuration of NATS (URL, nkey seed) is left to the user:
#   ~/.voicecli/voicecli.toml
#     [nats]
#     url = "nats://hub-host:4222"
#     nkey_seed_path = "~/.voicecli/nkeys/voice-client.seed"
#
# The wrapper reads those at invoke time (env still wins over the toml).

set -euo pipefail

# ── Args ──────────────────────────────────────────────────────────────────────
DE=""
KEY=""
BIN_DIR="$HOME/.local/bin"
CHECK_ONLY=0
QUIET=0

while [ $# -gt 0 ]; do
    case "$1" in
        --de) DE="$2"; shift 2 ;;
        --key) KEY="$2"; shift 2 ;;
        --bin-dir) BIN_DIR="$2"; shift 2 ;;
        --check-only) CHECK_ONLY=1; shift ;;
        --quiet) QUIET=1; shift ;;
        -h|--help)
            sed -n '2,/^set -e/p' "$0" | sed -n '/^#/p' | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

say() { [ "$QUIET" -eq 1 ] || echo "$@"; }
warn() { echo "WARN: $*" >&2; }
die()  { echo "ERROR: $*" >&2; exit 1; }

# ── Locate voiceCLI repo ──────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WRAPPER_SRC="$REPO_DIR/scripts/wrappers/voicecli-dictate.sh"
[ -f "$WRAPPER_SRC" ] || die "wrapper not found at $WRAPPER_SRC"

# ── DE detection ──────────────────────────────────────────────────────────────
if [ -z "$DE" ]; then
    case "${XDG_CURRENT_DESKTOP:-}" in
        *COSMIC*|*cosmic*) DE="cosmic" ;;
        *GNOME*|*gnome*)   DE="gnome"  ;;
        *)
            if [ -d "$HOME/.config/cosmic" ]; then DE="cosmic"
            elif command -v gsettings >/dev/null 2>&1; then DE="gnome"
            else die "could not detect desktop environment — pass --de cosmic|gnome"
            fi ;;
    esac
fi
[[ "$DE" =~ ^(cosmic|gnome)$ ]] || die "unsupported --de: $DE (cosmic|gnome)"

# Default keybinding format depends on DE.
if [ -z "$KEY" ]; then
    case "$DE" in
        cosmic) KEY="Ctrl+space" ;;
        gnome)  KEY="<Control>space" ;;
    esac
fi

say "Desktop: $DE"
say "Keybinding: $KEY"
say "Wrapper source: $WRAPPER_SRC"

# ── APT deps check ────────────────────────────────────────────────────────────
DEPS=(libnotify-bin wl-clipboard wtype gir1.2-gtklayershell-0.1)
MISSING=()
for p in "${DEPS[@]}"; do
    if ! dpkg-query -W -f='${Status}\n' "$p" 2>/dev/null | grep -q "install ok installed"; then
        MISSING+=("$p")
    fi
done
if [ ${#MISSING[@]} -gt 0 ]; then
    warn "Missing APT packages: ${MISSING[*]}"
    warn "Install with:  sudo apt install -y ${MISSING[*]}"
fi

# ── voicecli executable check ─────────────────────────────────────────────────
if ! command -v "$BIN_DIR/voicecli" >/dev/null 2>&1; then
    warn "voicecli not found at $BIN_DIR/voicecli"
    warn "Run from the voiceCLI repo:  uv sync --extra nats && ln -s \"\$PWD/.venv/bin/voicecli\" $BIN_DIR/voicecli"
fi

# ── ~/.voicecli/ existence (config + nkey) ────────────────────────────────────
TOML="$HOME/.voicecli/voicecli.toml"
SEED="$HOME/.voicecli/nkeys/voice-client.seed"
[ -f "$TOML" ] || warn "missing $TOML — set [nats] url + nkey_seed_path before first use"
[ -f "$SEED" ] || warn "missing nkey seed at $SEED — copy it from your hub-authorized machine (chmod 600)"

# ── Stop here when --check-only ───────────────────────────────────────────────
if [ "$CHECK_ONLY" -eq 1 ]; then
    say "check-only — no changes written"
    exit 0
fi

# ── Install wrapper ───────────────────────────────────────────────────────────
mkdir -p "$BIN_DIR"
WRAPPER_DST="$BIN_DIR/voicecli-dictate"
install -m 0755 "$WRAPPER_SRC" "$WRAPPER_DST"
say "Installed wrapper → $WRAPPER_DST"

# ── Bind shortcut ─────────────────────────────────────────────────────────────
case "$DE" in
    cosmic)
        CUSTOM="$HOME/.config/cosmic/com.system76.CosmicSettings.Shortcuts/v1/custom"

        # Parse "Mod1+Mod2+Key" → comma-separated modifiers + lower-case key.
        KEYNAME_LOWER="$(echo "${KEY##*+}" | tr '[:upper:]' '[:lower:]')"
        MODS_CSV=""
        IFS='+' read -ra parts <<< "${KEY%+*}"
        for m in "${parts[@]}"; do
            case "$(echo "$m" | tr '[:upper:]' '[:lower:]')" in
                ctrl|control)   MODS_CSV+="Ctrl," ;;
                shift)          MODS_CSV+="Shift," ;;
                alt)            MODS_CSV+="Alt," ;;
                super|win|meta) MODS_CSV+="Super," ;;
            esac
        done
        MODS_CSV="${MODS_CSV%,}"

        # Helper drops conflicting entries (same key+mods, same wrapper, or
        # legacy ``voicecli-dictate-nats``) before adding the new one.
        python3 "$REPO_DIR/scripts/_cosmic_bind.py" \
            "$CUSTOM" "$MODS_CSV" "$KEYNAME_LOWER" "$WRAPPER_DST" \
            "voiceCLI dictate" \
            voicecli-dictate-nats
        say "Wrote COSMIC binding → $CUSTOM"
        say "→ Logout/login (or restart cosmic-comp) to load the binding."
        ;;

    gnome)
        SCHEMA_BASE="org.gnome.settings-daemon.plugins.media-keys"
        ENTRY_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/voicecli-dictate/"
        ENTRY_SCHEMA="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$ENTRY_PATH"

        # Append entry path to the list if absent.
        existing="$(gsettings get $SCHEMA_BASE custom-keybindings 2>/dev/null || echo '@as []')"
        if [[ "$existing" != *"$ENTRY_PATH"* ]]; then
            new_list="$(printf '%s' "$existing" | sed "s|]\$|, '$ENTRY_PATH']|" | sed "s|@as \[\]|['$ENTRY_PATH']|")"
            gsettings set $SCHEMA_BASE custom-keybindings "$new_list"
        fi
        gsettings set "$ENTRY_SCHEMA" name "VoiceCLI Dictate"
        gsettings set "$ENTRY_SCHEMA" command "$WRAPPER_DST"
        gsettings set "$ENTRY_SCHEMA" binding "$KEY"
        say "Wrote GNOME binding via gsettings"
        ;;
esac

say "Done."
