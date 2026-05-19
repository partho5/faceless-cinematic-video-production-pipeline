#!/usr/bin/env python3
"""One-time audio analysis: detect loop_start_s and loop_end_s for every
track in assets/music/catalog.json and write the results back.

Algorithm (numpy-only, no librosa):
  1. Decode MP3 to mono float32 at 24 kHz via ffmpeg pipe.
  2. Compute short-time RMS envelope (0.5 s window, 0.1 s hop).
  3. Smooth envelope with a 1 s moving average (kills transient spikes).
  4. loop_start_s — first frame where smoothed RMS >= ACTIVE_THRESH (55% of
     peak) AND the next STABLE_WIN_S seconds stay >= STABLE_THRESH (40%).
     This reliably marks the end of quiet intros / slow builds.
  5. loop_end_s — last frame where smoothed RMS >= ACTIVE_THRESH, backed off
     by OUTRO_MARGIN_S (1.0 s) to land before the outro fade begins.
  6. Safety clamp: loop window must be >= 10 s; if detection produces
     something shorter, we expand to 80% of the track.

ASCII energy bar is printed for every track so you can spot-check results
before they are written. Tracks not on disk are skipped gracefully.

Usage:
    python scripts/analyze_music_loops.py            # analyze + update catalog
    python scripts/analyze_music_loops.py --dry-run  # print only, no write
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

# ------------------------------------------------------------------ config ---
REPO       = Path(__file__).resolve().parents[1]
ASSETS     = REPO / "assets"
CATALOG    = ASSETS / "music" / "catalog.json"
SR         = 24_000          # must match pipeline audio_util.SR

WINDOW_S   = 0.5             # RMS frame width
HOP_S      = 0.1             # RMS hop (10 frames/s → fine enough)
SMOOTH_S   = 1.0             # moving-average smoothing window

ACTIVE_THRESH  = 0.55        # fraction of peak RMS → "track is running"
STABLE_THRESH  = 0.40        # minimum to stay in the "groove"
STABLE_WIN_S   = 3.0         # groove must hold for this long to confirm start
OUTRO_MARGIN_S = 1.0         # back off from last active frame by this much
MIN_LOOP_S     = 10.0        # minimum legal loop window


# ------------------------------------------------------------------ audio ----

def decode_mp3(path: Path) -> np.ndarray | None:
    result = subprocess.run(
        ["ffmpeg", "-v", "error",
         "-i", str(path),
         "-f", "f32le", "-ar", str(SR), "-ac", "1", "-"],
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    return np.frombuffer(result.stdout, dtype=np.float32).copy()


def rms_envelope(audio: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Returns (times_s, rms_values)."""
    win = int(WINDOW_S * SR)
    hop = int(HOP_S  * SR)
    times, values = [], []
    for i in range(0, max(1, len(audio) - win), hop):
        chunk = audio[i : i + win]
        values.append(float(np.sqrt(np.mean(chunk ** 2))))
        times.append(i / SR)
    return np.array(times, np.float32), np.array(values, np.float32)


def smooth(rms: np.ndarray) -> np.ndarray:
    """Simple causal moving-average over SMOOTH_S seconds."""
    k = max(1, int(SMOOTH_S / HOP_S))
    kernel = np.ones(k, np.float32) / k
    return np.convolve(rms, kernel, mode="same")


# ---------------------------------------------------------- loop detection ---

def detect_loop_points(
    times: np.ndarray,
    rms:   np.ndarray,
    total_s: float,
) -> tuple[float, float]:
    if len(rms) == 0:
        return 0.0, float(total_s)

    peak = float(rms.max())
    if peak < 1e-6:                        # silent track
        return 0.0, float(total_s)

    active_thr  = peak * ACTIVE_THRESH
    stable_thr  = peak * STABLE_THRESH
    stable_f    = max(1, int(STABLE_WIN_S / HOP_S))

    # --- loop_start: first frame above active, then stable for STABLE_WIN_S ---
    loop_start_s = 0.0
    for i, r in enumerate(rms):
        if r >= active_thr:
            future = rms[i : i + stable_f]
            # require at least half the window to be present and all above stable
            if len(future) >= stable_f // 2 and np.all(future >= stable_thr):
                loop_start_s = max(0.0, float(times[i]) - HOP_S)
                break

    # --- loop_end: last frame above active, backed off by OUTRO_MARGIN_S ---
    loop_end_s = float(total_s)
    for i in range(len(rms) - 1, -1, -1):
        if rms[i] >= active_thr:
            # times[i] is the start of a 0.5 s window → add window + margin
            loop_end_s = min(float(total_s),
                             float(times[i]) + WINDOW_S + OUTRO_MARGIN_S)
            break

    # safety: ensure minimum loop window
    if loop_end_s - loop_start_s < MIN_LOOP_S:
        loop_end_s = min(float(total_s), loop_start_s + float(total_s) * 0.8)

    # sanity check: if detected start is >25% into the track the algorithm is
    # confused — typically a uniformly quiet ambient piece where a late spike
    # becomes the "peak". Fall back to full-track window in that case.
    if loop_start_s > total_s * 0.25:
        loop_start_s = 0.0
        loop_end_s   = round(total_s * 0.95, 1)

    return round(loop_start_s, 1), round(loop_end_s, 1)


# ----------------------------------------------------------- visualization ---

_BLOCKS = " ▁▂▃▄▅▆▇█"

def ascii_bar(rms: np.ndarray, width: int = 58) -> str:
    peak = float(rms.max()) if rms.max() > 0 else 1.0
    normed = rms / peak
    idx = np.linspace(0, len(normed) - 1, width).astype(int)
    return "".join(_BLOCKS[min(8, int(v * 8.4))] for v in normed[idx])


def print_track_report(
    slug: str,
    total_s: float,
    rms: np.ndarray,
    loop_start: float,
    loop_end: float,
) -> None:
    bar  = ascii_bar(rms)
    w    = len(bar)
    s_p  = min(w - 1, int(loop_start / total_s * w))
    e_p  = min(w - 1, int(loop_end   / total_s * w))

    dur_str = f"{int(total_s)//60}:{int(total_s)%60:02d}"
    print(f"\n  {slug}  ({dur_str})")
    print(f"  |{bar}|  ← smoothed RMS")

    # marker line
    marker = [" "] * w
    marker[s_p] = "^"
    if e_p != s_p:
        marker[e_p] = "^"
    print(f"   {''.join(marker)}")
    print(f"   loop_start={loop_start}s   loop_end={loop_end}s   "
          f"window={loop_end - loop_start:.0f}s")


# --------------------------------------------------------------- main -------

def analyze(dry_run: bool) -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    tracks  = catalog.get("tracks", [])

    results: dict[str, tuple[float, float]] = {}
    skipped: list[str] = []

    print(f"Analyzing {len(tracks)} tracks  "
          f"({'DRY RUN — no write' if dry_run else 'will update catalog'})\n"
          f"{'─' * 62}")

    for track in tracks:
        slug  = track["file"].removesuffix(".mp3")
        path  = ASSETS / "music" / track["file"]
        total = float(track.get("duration_sec", 0))

        if not path.exists():
            print(f"\n  {slug}  — NOT ON DISK (skipped)")
            skipped.append(slug)
            continue

        audio = decode_mp3(path)
        if audio is None:
            print(f"\n  {slug}  — decode failed (skipped)")
            skipped.append(slug)
            continue

        times, rms_raw = rms_envelope(audio)
        rms_smooth     = smooth(rms_raw)
        loop_start, loop_end = detect_loop_points(times, rms_smooth, total)

        print_track_report(slug, total, rms_smooth, loop_start, loop_end)
        results[slug] = (loop_start, loop_end)

    # ---------------------------------------------------------------- write --
    print(f"\n{'─' * 62}")
    print(f"Detected: {len(results)} tracks   Skipped: {len(skipped)}")

    if dry_run:
        print("DRY RUN — catalog not modified.")
        return

    changed = 0
    for track in tracks:
        slug = track["file"].removesuffix(".mp3")
        if slug in results:
            ls, le = results[slug]
            if (track.get("loop_start_s") != ls
                    or track.get("loop_end_s") != le):
                track["loop_start_s"] = ls
                track["loop_end_s"]   = le
                changed += 1

    catalog["version"] = "1.3"
    CATALOG.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"catalog.json updated  ({changed} tracks changed)  version→1.3")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="print analysis without modifying catalog.json")
    args = ap.parse_args()
    analyze(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
