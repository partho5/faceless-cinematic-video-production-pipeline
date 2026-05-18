"""G1: audio is the source of truth.

Planned start/end in the JSON are ordering hints only. After TTS we know
each segment's REAL spoken+padded duration, so we re-flow the timeline:
segments are laid back-to-back using measured audio length and every visual
is built against `seg.real_start/real_end`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..audio_util import duration_seconds
from ..schema.model import Segment


@dataclass
class Timeline:
    total_duration: float
    # segment_id -> (start, end) absolute seconds on the final timeline
    windows: dict[str, tuple[float, float]]


def reflow(segments: list[Segment]) -> Timeline:
    """Lay segments back-to-back by measured audio duration. Mutates segs."""
    cursor = 0.0
    windows: dict[str, tuple[float, float]] = {}
    for seg in segments:
        if not seg.audio_path:
            raise ValueError(f"{seg.id}: audio_path not set — run TTS before reflow")
        dur = duration_seconds(Path(seg.audio_path))
        seg.real_start = round(cursor, 4)
        seg.real_end = round(cursor + dur, 4)
        windows[seg.id] = (seg.real_start, seg.real_end)
        cursor += dur
    return Timeline(total_duration=round(cursor, 4), windows=windows)
