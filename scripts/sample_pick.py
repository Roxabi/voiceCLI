#!/usr/bin/env python3
"""Pick clean speech segments from an audio/video file for voice cloning.

Uses ffmpeg `silencedetect` to find speech boundaries, scores candidates by
duration · position · edge cleanliness, and emits preview MP3s + JSON.

Usage:
    uv run python scripts/sample_pick.py <input>
    uv run python scripts/sample_pick.py <input> --min-duration 15 --top-n 3
    uv run python scripts/sample_pick.py <input> --out-dir /tmp/picker --previews 10

Output (in --out-dir, default = <input-dir>/.sample-pick/):
    candidates.json        ranked list {rank, start, end, duration, score, reasons}
    preview_<rank>.mp3     first <previews>s of each top-N candidate
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path


_SILENCE_RE = re.compile(r"silence_(start|end): ([0-9.]+)")


@dataclass
class Segment:
    start: float
    end: float
    duration: float
    score: float
    reasons: list[str]
    rank: int = 0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["start"] = round(self.start, 2)
        d["end"] = round(self.end, 2)
        d["duration"] = round(self.duration, 2)
        d["score"] = round(self.score, 1)
        return d


def _check_ffmpeg() -> None:
    for cmd in ("ffmpeg", "ffprobe"):
        if shutil.which(cmd) is None:
            print(f"ERROR: {cmd} not in PATH", file=sys.stderr)
            sys.exit(1)


def get_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def detect_silence(
    path: Path,
    total_duration: float,
    noise_db: int = -35,
    min_silence: float = 0.5,
) -> list[tuple[float, float]]:
    """Return list of (silence_start, silence_end) via ffmpeg silencedetect.

    If the file ends mid-silence, `silencedetect` emits silence_start with no
    matching silence_end. We synthesize `end = total_duration` for that trailing
    segment so `speech_segments` correctly truncates the final speech window.
    """
    proc = subprocess.run(
        ["ffmpeg", "-i", str(path),
         "-af", f"silencedetect=n={noise_db}dB:d={min_silence}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    # silencedetect writes to stderr
    starts: list[float] = []
    ends: list[float] = []
    for match in _SILENCE_RE.finditer(proc.stderr):
        kind, val = match.group(1), float(match.group(2))
        (starts if kind == "start" else ends).append(val)
    pairs = list(zip(starts, ends))
    if len(starts) > len(ends):
        # File ended mid-silence — extend trailing unpaired start to EOF
        pairs.append((starts[len(ends)], total_duration))
    return pairs


def speech_segments(duration: float, silences: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Complement of silence intervals within [0, duration]."""
    if not silences:
        return [(0.0, duration)]
    segments: list[tuple[float, float]] = []
    cursor = 0.0
    for s_start, s_end in sorted(silences):
        if s_start > cursor:
            segments.append((cursor, s_start))
        cursor = max(cursor, s_end)
    if cursor < duration:
        segments.append((cursor, duration))
    return segments


def score_segment(
    start: float,
    end: float,
    total_duration: float,
    silences: list[tuple[float, float]],
) -> tuple[float, list[str]]:
    """Score a candidate speech segment. Higher = better."""
    duration = end - start
    reasons: list[str] = []
    score = 0.0

    # Duration: prefer 20-40s; saturates at 40s
    dur_score = min(duration / 40.0, 1.0) * 50.0
    score += dur_score
    reasons.append(f"dur={duration:.1f}s (+{dur_score:.0f})")

    # Position: skip first/last 10% (intros, outros)
    midpoint = (start + end) / 2
    if 0.10 * total_duration <= midpoint <= 0.90 * total_duration:
        score += 20.0
        reasons.append("mid-video (+20)")
    else:
        reasons.append("edge (+0)")

    # Edge cleanliness: prefer segments bordered by silence (vs hard cut)
    eps = 0.5
    left_clean = any(abs(s_end - start) < eps for _, s_end in silences) or start < eps
    right_clean = any(abs(s_start - end) < eps for s_start, _ in silences) or end > total_duration - eps
    if left_clean and right_clean:
        score += 15.0
        reasons.append("clean edges (+15)")
    elif left_clean or right_clean:
        score += 7.0
        reasons.append("half-clean (+7)")

    # Bonus: central third (peak attention / representativeness)
    if 0.33 * total_duration <= midpoint <= 0.67 * total_duration:
        score += 10.0
        reasons.append("central (+10)")

    return score, reasons


def pick_candidates(
    path: Path,
    min_duration: float = 15.0,
    top_n: int = 3,
    noise_db: int = -35,
    min_silence: float = 0.5,
) -> list[Segment]:
    total = get_duration(path)
    silences = detect_silence(path, total, noise_db=noise_db, min_silence=min_silence)
    segments = speech_segments(total, silences)
    candidates: list[Segment] = []
    for s_start, s_end in segments:
        dur = s_end - s_start
        if dur < min_duration:
            continue
        score, reasons = score_segment(s_start, s_end, total, silences)
        candidates.append(Segment(
            start=s_start, end=s_end, duration=dur, score=score, reasons=reasons,
        ))
    candidates.sort(key=lambda s: s.score, reverse=True)
    top = candidates[:top_n]
    for i, seg in enumerate(top, start=1):
        seg.rank = i
    return top


def emit_preview(src: Path, seg: Segment, out: Path, preview_duration: float = 10.0) -> None:
    clip_len = min(preview_duration, seg.duration)
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{seg.start}", "-t", f"{clip_len}",
         "-i", str(src), "-vn", "-acodec", "libmp3lame", "-q:a", "4",
         str(out)],
        capture_output=True, check=True,
    )


def format_timestamp(s: float) -> str:
    return f"{int(s) // 60}:{int(s) % 60:02d}"


def main() -> None:
    _check_ffmpeg()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", type=Path, help="Audio or video file")
    ap.add_argument("--min-duration", type=float, default=15.0,
                    help="Minimum speech-segment duration in seconds (default: 15)")
    ap.add_argument("--top-n", type=int, default=3, help="Number of candidates to return (default: 3)")
    ap.add_argument("--noise-db", type=int, default=-35,
                    help="Silence threshold in dB (default: -35; lower = stricter)")
    ap.add_argument("--min-silence", type=float, default=0.5,
                    help="Minimum silence duration to count as a boundary (default: 0.5s)")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="Output directory (default: <input-dir>/.sample-pick/)")
    ap.add_argument("--previews", type=float, default=10.0,
                    help="Preview clip length in seconds (default: 10; 0 = disable)")
    ap.add_argument("--quiet", action="store_true", help="JSON-only on stdout")
    args = ap.parse_args()

    src = args.input.expanduser().resolve()
    if not src.is_file():
        print(f"ERROR: file not found: {src}", file=sys.stderr)
        sys.exit(1)

    out_dir = args.out_dir or (src.parent / ".sample-pick")
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates = pick_candidates(
        src,
        min_duration=args.min_duration,
        top_n=args.top_n,
        noise_db=args.noise_db,
        min_silence=args.min_silence,
    )

    if not candidates:
        print("ERROR: no speech segments found above threshold.", file=sys.stderr)
        print(f"  Try lowering --min-duration (current: {args.min_duration}s)", file=sys.stderr)
        print(f"  Or loosening --noise-db (current: {args.noise_db}dB)", file=sys.stderr)
        sys.exit(2)

    # Emit previews
    preview_paths: dict[int, str] = {}
    if args.previews > 0:
        for seg in candidates:
            out = out_dir / f"preview_{seg.rank}.mp3"
            emit_preview(src, seg, out, preview_duration=args.previews)
            preview_paths[seg.rank] = str(out)

    # JSON output
    result = {
        "input": str(src),
        "out_dir": str(out_dir),
        "candidates": [
            {**seg.as_dict(), "preview": preview_paths.get(seg.rank)}
            for seg in candidates
        ],
    }
    json_path = out_dir / "candidates.json"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    # Human-readable
    if not args.quiet:
        print(f"input:   {src}")
        print(f"out-dir: {out_dir}\n")
        print(f"{'rank':<4} {'start':<8} {'end':<8} {'dur':<6} {'score':<6} reasons")
        print("-" * 100)
        for seg in candidates:
            start_ts = format_timestamp(seg.start)
            end_ts = format_timestamp(seg.end)
            print(f"{seg.rank:<4} {start_ts:<8} {end_ts:<8} "
                  f"{seg.duration:<6.1f} {seg.score:<6.1f} {' · '.join(seg.reasons)}")
        if preview_paths:
            print(f"\npreviews → {out_dir}/preview_<rank>.mp3")
        print(f"json     → {json_path}")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
