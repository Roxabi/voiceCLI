---
name: yt-clone
argument-hint: '<youtube-url> [--start <sec>] [--duration <sec>] [--slug <name>] [--test "<phrase>"]'
description: 'Clone a voice from a YouTube video — download audio, trim a clean sample, import it into voicecli, manage VRAM, and generate a test clone. Triggers: "clone from youtube" | "voice from youtube" | "yt clone" | "clone youtube voice" | "extract voice from video".'
version: 1.0.0
allowed-tools: Bash, Read, Glob, AskUserQuestion
---

# yt-clone — Voice Cloning from YouTube

Extract a voice from any YouTube video and set it as the active sample for `voicecli clone`.

## Entry

```
/yt-clone https://youtu.be/XYZ
/yt-clone https://youtu.be/XYZ --start 30 --duration 25
/yt-clone https://youtu.be/XYZ --slug my-voice --test "Bonjour, ceci est un test."
```

If no URL is provided → `AskUserQuestion` to get one.

## Arguments

| Arg | Default | Description |
|-----|---------|-------------|
| `<url>` | required | Any YouTube URL |
| `--start <sec>` | `5` | Start offset in seconds (skip intros/music) |
| `--duration <sec>` | `30` | Length of the sample to extract |
| `--slug <name>` | derived from video ID | Sample name (used as filename) |
| `--test "<phrase>"` | `"Bonjour, ceci est un test de clonage de voix."` | Phrase to generate after cloning |

## Step 1 — Parse Arguments

Extract URL and optional flags from `$ARGUMENTS`. Apply defaults for any missing args.
Derive `--slug` from the YouTube video ID if not provided (e.g. `yt_XYZ`).

## Step 2 — Check Dependencies

```bash
# yt-dlp
if ! command -v yt-dlp &>/dev/null; then
  if ! python3 -m yt_dlp --version &>/dev/null; then
    pip install yt-dlp -q
  fi
fi

# ffmpeg
command -v ffmpeg &>/dev/null || { echo "ERROR: ffmpeg not found"; exit 1; }

# voicecli
if command -v voicecli &>/dev/null; then
  VOICECLI="voicecli"
else
  for d in . .. ../voiceCLI ~/projects/voiceCLI; do
    test -f "$d/src/voicecli/cli.py" && VOICECLI_DIR="$(cd "$d" && pwd)" && break
  done
  [ -z "$VOICECLI_DIR" ] && echo "ERROR: voicecli not found" && exit 1
  VOICECLI="cd $VOICECLI_DIR && uv run voicecli"
fi
```

## Step 3 — Check & Free VRAM

The TTS daemon (qwen-fast) uses ~7.4 GB and the STT daemon uses ~2.2 GB on the RTX 3080 (10 GB total).
When both run simultaneously, `voicecli clone` fails with CUDA OOM.

```bash
FREE_VRAM=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | tr -d ' ')

if [ -n "$FREE_VRAM" ] && [ "$FREE_VRAM" -lt 500 ]; then
  echo "⚠ VRAM nearly full (${FREE_VRAM} MiB free) — stopping STT daemon..."
  make -C ~/projects/lyra-stack stt stop 2>/dev/null || true
  sleep 2
  FREE_VRAM=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | tr -d ' ')
  echo "  → ${FREE_VRAM} MiB free after stop"
  STT_WAS_STOPPED=1
else
  STT_WAS_STOPPED=0
fi
```

## Step 4 — Download Audio

```bash
TMP_DIR=$(mktemp -d /tmp/yt_clone_XXXXXX)

yt-dlp -x --audio-format wav --audio-quality 0 \
  -o "$TMP_DIR/audio.%(ext)s" \
  "$URL" 2>&1

# yt-dlp may output .wav directly or need conversion — find the file
AUDIO_FILE=$(ls "$TMP_DIR"/*.wav 2>/dev/null | head -1)
[ -z "$AUDIO_FILE" ] && AUDIO_FILE=$(ls "$TMP_DIR"/* 2>/dev/null | head -1)
```

If download fails (private video, geo-block, etc.) → inform the user with the error and stop.

## Step 5 — Trim Sample

Extract a clean `--duration`s segment starting at `--start`s, normalized to mono 22 050 Hz:

```bash
SAMPLE="$TMP_DIR/${SLUG}.wav"

ffmpeg -i "$AUDIO_FILE" \
  -ss "$START" -t "$DURATION" \
  -ac 1 -ar 22050 \
  "$SAMPLE" -y 2>&1
```

> **Tip**: avoid the first few seconds (music, intro jingle). Default `--start 5` is usually safe.
> For best cloning results, choose a segment with continuous clean speech and no background music.

## Step 6 — Import Sample

```bash
$VOICECLI samples add "$SAMPLE"
$VOICECLI samples use "${SLUG}.wav"
```

Confirm with: `$VOICECLI samples active`

## Step 7 — Generate Test Clone

```bash
$VOICECLI clone "$TEST_PHRASE" -e qwen-fast 2>&1
```

Capture the output path from the last line (`Saved to ...`) and report it.

## Step 8 — Restore STT Daemon

```bash
if [ "$STT_WAS_STOPPED" = "1" ]; then
  make -C ~/projects/lyra-stack stt start 2>/dev/null || true
  echo "✅ STT daemon restarted"
fi
```

## Step 9 — Report

Show a summary:

```
✅ Voice cloned from: <video title if available>

  Sample  : ~/.voicecli/TTS/samples/<slug>.wav  (30s · mono · 22 050 Hz)
  Engine  : qwen-fast
  Output  : ~/.voicecli/TTS/voices_out/...wav

To generate more with this voice:
  voicecli clone "Your text here" -e qwen-fast
  voicecli clone script.md -e qwen-fast
```

## Error Handling

| Error | Cause | Fix |
|-------|-------|-----|
| `Permission denied (publickey)` | SSH config issue, unrelated | Ignore — not needed for yt-clone |
| `CUDA out of memory` | STT daemon still running | Re-run Step 3 manually: `make -C ~/projects/lyra-stack stt stop` |
| `No supported JavaScript runtime` | yt-dlp warning, non-fatal | Safe to ignore — download proceeds |
| `ERROR: Video unavailable` | Private/geo-blocked video | Try a different URL or use a VPN |
| Sample sounds wrong | Bad segment (music/noise) | Re-run with `--start <sec>` to pick a cleaner moment |

## Notes

- **Sample quality is everything** — 15–30s of clean speech without background music gives the best clone.
- **qwen-fast is the default engine** — fastest, already warmed up in daemon. Switch to `chatterbox --lang French` for multilingual with better accent fidelity.
- **Active sample persists** — once set, all future `voicecli clone` calls reuse it until changed.
- The temporary download directory (`/tmp/yt_clone_*`) is left for inspection. Clean with `rm -rf /tmp/yt_clone_*`.

$ARGUMENTS
