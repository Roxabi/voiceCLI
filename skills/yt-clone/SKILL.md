---
name: yt-clone
argument-hint: '<youtube-url> [--start <sec>] [--duration <sec>] [--slug <name>] [--test "<phrase>"] [--blind]'
description: 'Clone a voice from a YouTube video — download audio, auto-pick a clean segment (via sample-pick), import it into voicecli, manage VRAM, and generate a test clone. Triggers: "clone from youtube" | "voice from youtube" | "yt clone" | "clone youtube voice" | "extract voice from video".'
version: 1.1.0
allowed-tools: Bash, Read, Glob
---

# yt-clone — Voice Cloning from YouTube

Extract a voice from any YouTube video and set it as the active sample for `voicecli clone`.

**v1.1:** segment selection is now automated via `sample-pick` (silence-based VAD + scoring). Pass `--start`/`--duration` to override, or `--blind` to restore the pre-v1.1 naive trim.

## Entry

```
/yt-clone https://youtu.be/XYZ                              # auto-pick clean segment
/yt-clone https://youtu.be/XYZ --start 30 --duration 25     # manual override
/yt-clone https://youtu.be/XYZ --blind                      # pre-v1.1 behavior (start=5, dur=30)
/yt-clone https://youtu.be/XYZ --slug my-voice --test "Bonjour, ceci est un test."
```

If no URL is provided → ask the user for one (plain text) and wait for reply.

## Arguments

| Arg | Default | Description |
|-----|---------|-------------|
| `<url>` | required | Any YouTube URL |
| `--start <sec>` | *auto-pick* | Manual override — start offset in seconds (disables picker) |
| `--duration <sec>` | *auto-pick* | Manual override — length of the sample to extract |
| `--slug <name>` | derived from video ID | Sample name (used as filename) |
| `--test "<phrase>"` | `"Bonjour, ceci est un test de clonage de voix."` | Phrase to generate after cloning |
| `--blind` | off | Skip picker, use legacy defaults (`start=5`, `duration=30`) |

## Step 1 — Parse Arguments

Extract URL and optional flags from `$ARGUMENTS`. Apply defaults for any missing args.
Derive `--slug` from the YouTube video ID if not provided (e.g. `yt_XYZ`).

**Flag precedence guard** — reject ambiguous combinations before doing anything:

```
--blind ∧ (--start ∨ --duration)  → ERROR "--blind is exclusive with --start/--duration"
--start XOR --duration            → ERROR "--start and --duration must be used together"
```

Enforce in bash before mode selection:

```bash
if [ -n "$BLIND" ] && { [ -n "$START" ] || [ -n "$DURATION" ]; }; then
  echo "ERROR: --blind is exclusive with --start/--duration" >&2; exit 1
fi
if { [ -n "$START" ] && [ -z "$DURATION" ]; } || { [ -z "$START" ] && [ -n "$DURATION" ]; }; then
  echo "ERROR: --start and --duration must be used together (or use neither for auto-pick)" >&2; exit 1
fi
```

Determine **selection mode:**

```
--start ∧ --duration ∃       → mode = "manual"       (user override, skip picker)
--blind ∃                     → mode = "blind"        (start=5, duration=30, skip picker)
else                          → mode = "auto-pick"    (run sample-pick on downloaded audio)
```

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
  for d in ../voiceCLI ~/projects/voiceCLI; do
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

## Step 4.5 — Auto-pick Segment (mode = auto-pick)

When no `--start`/`--duration` override, run the clean-segment picker on the full downloaded audio:

```bash
cd "$VOICECLI_DIR" && uv run python scripts/sample_pick.py "$AUDIO_FILE" \
  --top 1 --min-duration 15 --out-dir "$TMP_DIR/.sample-pick"
```

Read top-1 candidate from `$TMP_DIR/.sample-pick/candidates.json`:

```bash
START=$(jq -r '.candidates[0].start' "$TMP_DIR/.sample-pick/candidates.json")
DURATION=$(jq -r '.candidates[0].duration' "$TMP_DIR/.sample-pick/candidates.json")
SCORE=$(jq -r '.candidates[0].score' "$TMP_DIR/.sample-pick/candidates.json")

# Cap duration at 30s (enough for clone quality, keeps load fast)
DURATION=$(awk -v d="$DURATION" 'BEGIN{print (d > 30 ? 30 : d)}')

echo "  auto-picked segment: start=${START}s · duration=${DURATION}s · score=${SCORE}"
```

**Picker empty** (exit 2) → fall back to blind defaults (`START=5`, `DURATION=30`), warn the user. This matches pre-v1.1 behavior.

**Skipped for manual / blind modes.**

## Step 5 — Trim Sample

Extract the selected segment, normalized to mono 22 050 Hz:

```bash
SAMPLE="$TMP_DIR/${SLUG}.wav"

ffmpeg -i "$AUDIO_FILE" \
  -ss "$START" -t "$DURATION" \
  -ac 1 -ar 22050 \
  "$SAMPLE" -y 2>&1
```

> **Auto-pick** (default): segment is already scored for clean edges + mid-video position.
> **Manual override**: user-provided `--start`/`--duration` — assumed correct, no check.
> **Blind mode**: pre-v1.1 behavior, `start=5` is a best-guess skip of intros/music.

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
| Sample sounds wrong | Picker chose a bad segment (rare) | Re-run with `--start <sec> --duration <sec>` manual override |
| `no speech segments found` | All-music video or unusually low-volume speech | Retry with `--blind` or manual `--start`/`--duration` |

## Notes

- **Sample quality is everything** — 15–30s of clean speech without background music gives the best clone. The picker defaults to this range.
- **Picker details** — see [`sample-pick`](../sample-pick/SKILL.md) for scoring rationale (duration · position · edge cleanliness) and how to call it standalone on local files.
- **qwen-fast is the default engine** — fastest, already warmed up in daemon. Switch to `chatterbox --lang French` for multilingual with better accent fidelity.
- **Active sample persists** — once set, all future `voicecli clone` calls reuse it until changed.
- The temporary download directory (`/tmp/yt_clone_*`) is left for inspection. Clean with `rm -rf /tmp/yt_clone_*`.

$ARGUMENTS
