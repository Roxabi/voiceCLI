#!/usr/bin/env bash
# ensure-pulse.sh — guarantee a working PulseAudio server.
# Tries WSLg first, falls back to a local PulseAudio instance.
# Source this from daemon wrappers: `source ensure-pulse.sh`

_pulse_ok() {
    pactl info >/dev/null 2>&1
}

# 1. Try WSLg PulseAudio (default on WSL2)
if [ -S "/mnt/wslg/PulseServer" ]; then
    export PULSE_SERVER="unix:/mnt/wslg/PulseServer"
    if _pulse_ok; then
        return 0 2>/dev/null || exit 0
    fi
    echo "[ensure-pulse] WSLg PulseAudio not responding, falling back to local" >&2
fi

# 2. Fall back to local PulseAudio — must fully unset PULSE_SERVER (empty string = invalid)
unset PULSE_SERVER

# Remove stale WSLg symlinks that block local PulseAudio startup
_pulse_runtime="/run/user/$(id -u)/pulse"
if [ -d "$_pulse_runtime" ]; then
    for f in "$_pulse_runtime"/{native,pid}; do
        [ -L "$f" ] && ! [ -e "$f" ] && rm -f "$f"
    done
fi

if _pulse_ok; then
    echo "[ensure-pulse] Using existing local PulseAudio" >&2
    return 0 2>/dev/null || exit 0
fi

echo "[ensure-pulse] Starting local PulseAudio server" >&2
pulseaudio --start --exit-idle-time=-1 2>&1 | grep -v "^W:" >&2

if _pulse_ok; then
    echo "[ensure-pulse] Local PulseAudio started OK" >&2
else
    echo "[ensure-pulse] WARNING: No working PulseAudio found" >&2
fi
