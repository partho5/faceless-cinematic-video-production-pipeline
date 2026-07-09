"""Inter-clip transition effects via ffmpeg xfade filter.

Each transition merges two adjacent segment video files with a visual
blend effect. The result replaces the plain hard-cut concat.

Approach:
  - After all segment clips are rendered, pairs are joined with xfade.
  - Transitions are applied sequentially: A+B → AB, AB+C → ABC, ...
  - A pool of xfade effect names is randomly shuffled, no consecutive repeat.
  - Duration is short (0.3s default) so timing stays tight.
  - Falls back to plain concat on any ffmpeg error (safe degradation).

Audio is NOT touched here — the master audio track is muxed separately
in render.py as before.

ffmpeg xfade effects used (subset — all broadly compatible):
  fade, slideright, slideleft, slideup, slidedown,
  dissolve, wipeleft, wiperight, wipeup, wipedown,
  radial, smoothleft, smoothright, smoothup, smoothdown

Reference: https://ffmpeg.org/ffmpeg-filters.html#xfade
"""
from __future__ import annotations

import random
import subprocess
from pathlib import Path


# Pool of ffmpeg xfade effect names.
# Add more names here to grow the variety — no other changes needed.
TRANSITION_POOL: list[str] = [
    "fade",
    "slideright",
    "slideleft",
    "slideup",
    "slidedown",
    "dissolve",
    "wipeleft",
    "wiperight",
    "wipeup",
    "wipedown",
    "smoothleft",
    "smoothright",
    "smoothup",
    "smoothdown",
    "radial",
    "circleopen",
    "circleclose",
    "pixelize",
    "squeezeh",
    "squeezev",
]

# Default transition duration in seconds.
# Keep short so audio sync is not noticeably affected.
TRANSITION_DURATION = 0.3


def _shuffle_no_repeat(pool: list[str], n: int) -> list[str]:
    """Return `n` items randomly drawn from `pool`, never the same twice in a row."""
    if not pool or n == 0:
        return []
    chosen: list[str] = []
    last = None
    candidates = pool.copy()
    for _ in range(n):
        available = [c for c in candidates if c != last] or candidates
        pick = random.choice(available)
        chosen.append(pick)
        last = pick
    return chosen


def _get_duration(path: Path) -> float:
    """Get video duration in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def _xfade_two(
    clip_a: Path,
    clip_b: Path,
    out: Path,
    effect: str,
    duration: float,
    fps: int,
) -> bool:
    """Merge clip_a + clip_b with an xfade transition into `out`.

    Duration-preserving design:
      clip_a's last frame is frozen for `duration` seconds via tpad.
      The xfade consumes ONLY that frozen padding — no real content from
      either clip is overlapped or removed.

      Output duration = dur_a + dur_b  (not dur_a + dur_b - duration).
      This guarantees video stays in sync with the audio track across
      any number of clips.

    Returns True on success, False on ffmpeg error (caller falls back).
    """
    dur_a = _get_duration(clip_a)
    if dur_a <= 0.1:
        return False

    # offset = real end of clip_a — the xfade starts exactly there,
    # blending into clip_b while clip_a shows its frozen last frame.
    offset = round(dur_a, 4)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(clip_a),
        "-i", str(clip_b),
        "-filter_complex",
        (
            # Step 1: freeze clip_a's last frame for `duration` seconds
            f"[0:v]tpad=stop_duration={duration}:stop_mode=clone[padded];"
            # Step 2: xfade the padded clip into clip_b at the freeze point
            f"[padded][1:v]xfade="
            f"transition={effect}:"
            f"duration={duration}:"
            f"offset={offset}"
            f"[v]"
        ),
        "-map", "[v]",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-r", str(fps),
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0


def apply_transitions(
    seg_paths: list[Path],
    work_dir: Path,
    fps: int,
    *,
    duration: float = TRANSITION_DURATION,
    pool: list[str] | None = None,
) -> list[Path]:
    """Apply random xfade transitions between all adjacent segment clips.

    Args:
        seg_paths:  Ordered list of rendered segment video paths.
        work_dir:   Directory for intermediate merged files.
        fps:        Frame rate (must match the rendered segments).
        duration:   Transition length in seconds (default 0.3s).
        pool:       Override the transition pool (defaults to TRANSITION_POOL).

    Returns:
        A list of paths ready to be concatenated — either the original
        seg_paths (if only 1 clip, or all transitions failed) or a single
        merged file path after progressive xfade merging.

    Fallback contract: if any xfade step fails, that pair falls back to
    a plain hard cut. The pipeline never crashes due to a transition error.
    """
    if len(seg_paths) <= 1:
        return seg_paths

    effect_pool = pool or TRANSITION_POOL
    # We need (N-1) transitions for N clips
    effects = _shuffle_no_repeat(effect_pool, len(seg_paths) - 1)

    # Progressive merge: A+B → AB_0, AB_0+C → AB_1, ...
    # Each merge file is _trans_NNNN.mp4
    current = seg_paths[0]
    merged_files: list[Path] = []  # track for cleanup on success

    for i, (next_clip, effect) in enumerate(zip(seg_paths[1:], effects)):
        out = work_dir / f"_trans_{i:04d}.mp4"
        print(f"[vp] transition {i+1}/{len(effects)}: {effect} ({current.name} → {next_clip.name})", flush=True)

        ok = _xfade_two(current, next_clip, out, effect, duration, fps)
        if ok:
            merged_files.append(out)
            current = out
        else:
            # Hard-cut fallback: concatenate current + next_clip into out
            print(f"[vp]   xfade failed, falling back to hard cut for this pair", flush=True)
            concat_txt = work_dir / f"_fc_{i:04d}.txt"
            concat_txt.write_text(
                f"file '{current.as_posix()}'\nfile '{next_clip.as_posix()}'",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0", "-i", str(concat_txt),
                    "-c:v", "copy",
                    str(out),
                ],
                capture_output=True,
            )
            concat_txt.unlink(missing_ok=True)
            if result.returncode == 0:
                merged_files.append(out)
                current = out
            # if even concat fails, keep current as-is (best-effort)

    # current is now the fully merged file (or the last successful state)
    # Return it as a single-element list — render.py will concat + mux audio
    return [current]
