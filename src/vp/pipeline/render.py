"""Core render engine (planning/02 component 8).

Skeleton responsibilities (this component):
  - reflow timeline from measured audio (G1),
  - per-segment visual via a PLUGGABLE source (Pexels slots in at comp 4;
    skeleton = mood-colour background so output is never black -> QA-safe),
  - a frame-transform CHAIN that comps 5-9 register into (text, camera,
    LUT/grain, etc.) without touching this file,
  - concatenate, mux the real voice track, export MP4.

Quality presets (G6): `final` = 1920x1080@30, `preview` = 960x540@15 (fast
smoke/e2e). Visuals are built against seg.real_start/real_end only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from ..audio_util import SR, read_wav, write_wav
from ..config import ASSETS, Config
from ..schema.model import ControlDocument, Segment
from .align import Alignment
from .timeline import Timeline, reflow

# frame transform: (segment, frame[H,W,3 uint8], local_t, ctx) -> frame
FrameXform = Callable[[Segment, np.ndarray, float, "RenderContext"], np.ndarray]
# visual source: (segment, ctx) -> make_frame(local_t)->frame[H,W,3 uint8]
VisualSource = Callable[[Segment, "RenderContext"], Callable[[float], np.ndarray]]

PRESETS = {
    "final": dict(w=1920, h=1080, fps=30),
    "preview": dict(w=960, h=540, fps=15),
}

# base mood colours so the skeleton/fallback frame reads as the right grade
_GRADE_RGB = {
    "cold_isolation": (28, 38, 54), "warm_comfort": (74, 56, 38),
    "warm_comfort_dark": (46, 34, 24), "clinical": (30, 46, 50),
    "surveillance": (22, 30, 28), "threat": (60, 20, 22),
    "interrogation": (52, 50, 44), "revelation": (70, 66, 52),
    "memory": (44, 42, 50), "madness": (50, 22, 46), "dream": (40, 44, 60),
    "death": (24, 24, 24), "nostalgia": (58, 46, 34),
}


@dataclass
class RenderContext:
    cfg: Config
    doc: ControlDocument
    timeline: Timeline
    alignments: dict[str, Alignment]
    work_dir: Path
    w: int
    h: int
    fps: int
    assets_root: Path
    clip_provider: object | None = None  # set by comp 4
    extras: dict = field(default_factory=dict)


def _grade_color(seg: Segment, doc: ControlDocument) -> tuple[int, int, int]:
    g = seg.color_grade_override or doc.video_meta.get("base_color_grade", "cold_isolation")
    return _GRADE_RGB.get(g, (30, 30, 36))


def mood_background_source(seg: Segment, ctx: RenderContext) -> Callable[[float], np.ndarray]:
    """Skeleton visual: vertical-gradient mood field (replaced by Pexels)."""
    r, g, b = _grade_color(seg, ctx.doc)
    h, w = ctx.h, ctx.w
    grad = np.linspace(0.7, 1.15, h, dtype=np.float32)[:, None]
    base = np.zeros((h, w, 3), np.float32)
    base[..., 0], base[..., 1], base[..., 2] = r, g, b
    base *= grad[..., None]
    base = np.clip(base, 0, 255).astype(np.uint8)

    def make(local_t: float) -> np.ndarray:
        return base.copy()

    return make


class RenderEngine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.visual_source: VisualSource = mood_background_source
        self.frame_chain: list[FrameXform] = []

    def register_xform(self, fn: FrameXform) -> None:
        self.frame_chain.append(fn)

    # -- audio ---------------------------------------------------------------
    def _build_voice_track(self, segments: list[Segment], out: Path) -> float:
        chunks = []
        for seg in segments:
            a, sr = read_wav(Path(seg.audio_path))
            if sr != SR:  # keep one rate across the track
                idx = (np.arange(int(len(a) * SR / sr)) * sr / SR).astype(int)
                a = a[np.clip(idx, 0, len(a) - 1)]
            chunks.append(a)
        track = np.concatenate(chunks) if chunks else np.zeros(1, np.float32)
        write_wav(out, track, SR)
        return len(track) / SR

    # -- main ---------------------------------------------------------------
    def render(
        self,
        doc: ControlDocument,
        alignments: dict[str, Alignment],
        out_path: Path,
        *,
        preset: str = "preview",
        work_dir: Path | None = None,
        audio_track: Path | None = None,
    ) -> dict:
        from moviepy.editor import AudioFileClip, VideoClip, concatenate_videoclips

        p = PRESETS[preset]
        work = work_dir or out_path.parent / "_work"
        work.mkdir(parents=True, exist_ok=True)

        tl = reflow(doc.segments)
        ctx = RenderContext(
            cfg=self.cfg, doc=doc, timeline=tl, alignments=alignments,
            work_dir=work, w=p["w"], h=p["h"], fps=p["fps"],
            assets_root=ASSETS,
        )

        clips = []
        for seg in doc.segments:
            dur = max(0.1, seg.duration)
            base_make = self.visual_source(seg, ctx)
            chain = self.frame_chain

            def make_frame(t: float, seg=seg, base_make=base_make, chain=chain):
                frame = base_make(t)
                for fn in chain:
                    frame = fn(seg, frame, t, ctx)
                return frame

            clips.append(VideoClip(make_frame, duration=dur).set_fps(p["fps"]))

        video = concatenate_videoclips(clips, method="chain")

        voice = audio_track or (work / "voice.wav")
        if not voice.exists():
            self._build_voice_track(doc.segments, voice)
        video = video.set_audio(AudioFileClip(str(voice)))

        out_path.parent.mkdir(parents=True, exist_ok=True)
        video.write_videofile(
            str(out_path), fps=p["fps"], codec="libx264", audio_codec="aac",
            preset="ultrafast", logger=None, threads=4,
        )
        for c in clips:
            c.close()
        video.close()
        return {
            "path": str(out_path),
            "duration": round(tl.total_duration, 3),
            "segments": len(doc.segments),
            "preset": preset,
            "resolution": f"{p['w']}x{p['h']}",
            "fps": p["fps"],
        }
