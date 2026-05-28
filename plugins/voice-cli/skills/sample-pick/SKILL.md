---
name: sample-pick
argument-hint: '<audio-or-video-file> [--top 3] [--min-duration 15] [--auto] [--slug <name>]'
description: 'Pick a clean speech segment from any audio or video file for voice cloning — silence-based VAD, scoring by duration / position / edge-cleanliness, preview MP3s. Replaces blind `--start`/`--duration` guessing. Triggers: "sample pick" | "pick sample" | "clean segment" | "find speech" | "pick voice segment" | "extract clean audio".'
version: 1.0.0
allowed-tools: Bash, Read, Glob
---

# sample-pick — Clean Speech Segment Picker

Pick the best clean speech segment from an audio or video file for voice cloning. **Source-agnostic** — works on any local file (YouTube needs `yt-clone` which calls this internally).

## Entry

```
/sample-pick ~/Videos/talk.mp4
/sample-pick voice.wav --top 3
/sample-pick recording.m4a --auto --slug my-voice      # auto-pick top 1, import
/sample-pick audio.wav --min-duration 20
```

¬ path → DP(B) for file path.

## Arguments

| Arg | Default | Description |
|-----|---------|-------------|
| `<input>` | required | Audio or video file (any format ffmpeg reads) |
| `--top <N>` | `3` | Number of candidates to return |
| `--min-duration <sec>` | `15` | Minimum speech-segment duration |
| `--noise-db <dB>` | `-35` | Silence threshold (lower = stricter) |
| `--auto` | off | Skip preview/choice, auto-pick top-1, import + set active |
| `--slug <name>` | derived from filename | Sample name for voicecli |

## Step 1 — Check Dependencies

```bash
# ffmpeg / ffprobe
command -v ffmpeg &>/dev/null || { echo "ERROR: ffmpeg not found"; exit 1; }
command -v ffprobe &>/dev/null || { echo "ERROR: ffprobe not found"; exit 1; }

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

## Step 2 — Locate voiceCLI

```bash
if [ -z "$VOICECLI_DIR" ]; then
  for d in ../voiceCLI ~/projects/voiceCLI; do
    test -f "$d/scripts/sample_pick.py" && VOICECLI_DIR="$(cd "$d" && pwd)" && break
  done
  [ -z "$VOICECLI_DIR" ] && echo "ERROR: voiceCLI repo not found" && exit 1
fi
```

## Step 3 — Run Picker

```bash
cd "$VOICECLI_DIR" && uv run python scripts/sample_pick.py "$INPUT" \
  --top "$TOP_N" --min-duration "$MIN_DUR"
```

Outputs:

```
<input-dir>/.sample-pick/
  candidates.json         ranked list
  preview_1.mp3           top-ranked (first 10s)
  preview_2.mp3           runner-up
  preview_3.mp3           third
```

**Empty result** (no segments ≥ `--min-duration`):
- Suggest lowering `--min-duration` (currently $MIN_DUR s)
- Suggest loosening `--noise-db` (try `-40`)
- Stop.

## Step 4 — Present Candidates

Render table from `candidates.json`:

```
rank  start  end    dur   score  reasons
1     3:30   4:15   45.0s 85.0   dur=45s (+50) · mid-video (+20) · clean edges (+15) · central (+10)
2     8:10   8:40   30.0s 75.0   dur=30s (+38) · mid-video (+20) · clean edges (+15) · central (+10)
3     1:45   2:05   20.0s 55.0   dur=20s (+25) · mid-video (+20) · clean edges (+15) · edge (+0)
```

Show preview paths: `<input-dir>/.sample-pick/preview_1.mp3` etc. User can listen via any audio player.

## Step 5 — User Choice

`--auto` ∃ → select rank 1, skip to Step 6.
`--auto` ∄ → present choices:

```
Which segment? (1-N · 'p <rank>' to re-preview · 'a' to auto-pick best · 's' to skip import)
```

- Wait for user input
- Validate choice

## Step 6 — Extract + Import

Derive slug (if not provided): basename of input, lowercase, replace non-alphanum with `_`.

```bash
CHOICE=<user-picked-rank>
START=$(jq -r ".candidates[$((CHOICE-1))].start" "$OUT_DIR/candidates.json")
DUR=$(jq -r ".candidates[$((CHOICE-1))].duration" "$OUT_DIR/candidates.json")

# Cap at 30s max for cloning (more than enough)
DUR=$(awk -v d="$DUR" 'BEGIN{print (d > 30 ? 30 : d)}')

SAMPLE="/tmp/sample_${SLUG}.wav"
ffmpeg -y -ss "$START" -t "$DUR" -i "$INPUT" \
  -ac 1 -ar 22050 -acodec pcm_s16le "$SAMPLE" 2>&1 | tail -5

$VOICECLI samples add "$SAMPLE"
$VOICECLI samples use "${SLUG}.wav"
```

## Step 7 — Report

```
✅ Sample picked & imported

  Source   : <input>
  Segment  : <start> → <end>  (<duration>s)
  Score    : <score>
  Sample   : ~/.roxabi/voicecli/TTS/samples/<slug>.wav
  Active   : yes

To clone with this voice:
  voicecli clone "Your text here" -e qwen-fast
```

Optional follow-up: suggest running `voicecli clone` with a test phrase.

## Scoring rationale

| Factor | Range | Why |
|---|---|---|
| Duration | 0-50 pts, saturates at 40s | 15-30s is the clone-quality sweet spot |
| Mid-video | +20 | Skip intros/outros (first/last 10%) |
| Clean edges | +15 (both) · +7 (one) | Neighbors are silence → stable start/end, no mid-word cut |
| Central third | +10 | Representative of main content, not outro ramble |

**Not yet implemented (v2):** music detection (energy in non-vocal bands), SNR scoring, speaker-diarization for multi-voice sources.

## Error Handling

| Error | Cause | Fix |
|-------|-------|-----|
| `no speech segments found` | All silence below threshold | Lower `--min-duration` or loosen `--noise-db` |
| `ffmpeg: No such file` | Bad input path | Check path, resolve symlinks |
| `voicecli not found` | Not installed / VOICECLI_DIR unset | `pip install voicecli` or run from voiceCLI repo |

## Notes

- Preview MP3s at `<input-dir>/.sample-pick/preview_*.mp3` — safe to delete after import
- Output WAV normalized: mono · 22050 Hz · 16-bit PCM (voicecli default)
- Capped at 30s duration even if segment is longer — enough for a clone, faster load
- For YouTube sources → use `/yt-clone` (wraps this + download)

$ARGUMENTS
