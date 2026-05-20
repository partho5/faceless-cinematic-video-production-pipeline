"""Clip sourcing (planning/07 §C, G4/G5).

Real path: Pexels video search -> filter (landscape, >=1080p, dur>=segment)
-> download -> disk cache by Pexels id -> auto-cut from mid-section ->
montage support -> anti-repetition (no clip id reused within a video) ->
generic mood fallback derived from beat_type.

Offline path (no PEXELS_API_KEY / no network): a deterministic PROCEDURAL
clip bank — query-seeded animated texture fields — so the renderer gets
*moving* B-roll with variety, never a static frame, and the pipeline runs
fully without keys.

Exposes `ClipProvider.visual_source` which plugs straight into RenderEngine.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from ..config import CLIP_CACHE, Config
from ..schema.model import Segment

_GENERIC_BY_BEAT = {
    "hook": "dark abstract smoke", "chapter_open": "slow dark clouds",
    "list_rhythm": "city lights bokeh", "revelation_cold": "shadow figure dark",
    "explanation": "ink water spreading", "deepening": "empty room dim",
    "direct_address": "face close up dark", "shift_to_cold": "fog cold light",
    "wisdom_contrast": "candle flame dark", "setup": "rain window night",
}


def _seed(s: str) -> int:
    return int(hashlib.sha1(s.encode()).hexdigest()[:8], 16)


@dataclass
class ClipRef:
    source: str          # "pexels" | "procedural"
    id: str
    query: str
    path: Path | None = None
    meta: dict = field(default_factory=dict)


class AntiRepetition:
    """No clip id reused within one video (planning/07 §C.5)."""

    def __init__(self) -> None:
        self.used: set[str] = set()

    def fresh(self, cid: str) -> bool:
        return cid not in self.used

    def mark(self, cid: str) -> None:
        self.used.add(cid)


# --------------------------------------------------------------- procedural --
def _procedural_base(query: str, w: int, h: int) -> np.ndarray:
    """A query-distinct textured field (BGR-ish mood), animated by scroll."""
    rng = np.random.default_rng(_seed(query))
    # low-freq cloud via upsampled noise
    small = rng.random((9, 16, 3)).astype(np.float32)
    ys = (np.linspace(0, 8, h)).astype(int)
    xs = (np.linspace(0, 15, w)).astype(int)
    field = small[np.clip(ys, 0, 8)][:, np.clip(xs, 0, 15)]
    # blur-ish: average with shifted copies
    for _ in range(2):
        field = (field + np.roll(field, 7, 0) + np.roll(field, 11, 1)) / 3.0
    tint = np.array(
        [60 + (_seed(query) % 90), 50 + (_seed(query[::-1]) % 80), 70], np.float32
    )
    img = np.clip(field * tint * 2.2, 0, 255).astype(np.uint8)
    return img


class ClipProvider:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.spec_offline = not cfg.env("PEXELS_API_KEY")
        self.cache_dir = CLIP_CACHE
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.anti = AntiRepetition()
        self.manifest: list[dict] = []  # provenance for render_manifest (G11)
        self._session = None

    # -- real Pexels (lazy) ------------------------------------------------
    def _search_pexels(self, query: str, min_dur: float) -> ClipRef | None:
        import requests  # lazy

        if self._session is None:
            self._session = requests.Session()
            self._session.headers["Authorization"] = self.cfg.env("PEXELS_API_KEY")
        r = self._session.get(
            "https://api.pexels.com/videos/search",
            params={"query": query, "orientation": "landscape",
                    "size": "large", "per_page": 15},
            timeout=20,
        )
        r.raise_for_status()
        for v in r.json().get("videos", []):
            if v.get("width", 0) < 1920 or v.get("height", 0) < 1080:
                continue
            cid = str(v["id"])
            if not self.anti.fresh(cid):
                continue
            files = [f for f in v["video_files"] if f.get("height", 0) <= 1080]
            files.sort(key=lambda f: f.get("height", 0), reverse=True)
            if not files:
                continue
            dst = self.cache_dir / f"pexels_{cid}.mp4"
            if not dst.exists():
                data = self._session.get(files[0]["link"], timeout=60).content
                dst.write_bytes(data)
            return ClipRef("pexels", cid, query, dst,
                           {"duration": v.get("duration", 0)})
        return None

    def _resolve(self, query: str, backup: str, beat: str,
                 min_dur: float) -> ClipRef:
        if not self.spec_offline:
            for q in (query, backup,
                      _GENERIC_BY_BEAT.get(beat, "dark abstract texture")):
                if not q:
                    continue
                try:
                    ref = self._search_pexels(q, min_dur)
                except Exception:
                    ref = None
                if ref:
                    self.anti.mark(ref.id)
                    return ref
        # offline / nothing found -> procedural, query-seeded, kept distinct
        base_q = query or backup or _GENERIC_BY_BEAT.get(beat, "dark texture")
        cid = f"proc_{_seed(base_q)}"
        n = 0
        while not self.anti.fresh(cid):  # guarantee in-video variety
            n += 1
            cid = f"proc_{_seed(base_q + str(n))}"
        self.anti.mark(cid)
        return ClipRef("procedural", cid, base_q, None, {"variant": n})

    # -- frame functions ---------------------------------------------------
    def _make_frame_fn(self, ref: ClipRef, w: int, h: int,
                       dur: float) -> Callable[[float], np.ndarray]:
        if ref.source == "pexels" and ref.path:
            from moviepy import VideoFileClip  # lazy

            vc = VideoFileClip(str(ref.path))
            clip_len = vc.duration or dur
            # take from mid-section, skip 0.5s head/tail (planning/07 §C.4)
            head = 0.5 if clip_len > dur + 1.0 else 0.0
            start = min(head, max(0.0, clip_len - dur))

            def make(local_t: float, vc=vc, start=start, clip_len=clip_len):
                tt = start + local_t
                if tt >= clip_len:  # loop short clips
                    tt = start + (local_t % max(0.1, clip_len - start))
                f = vc.get_frame(tt)
                if f.shape[0] != h or f.shape[1] != w:
                    yi = np.linspace(0, f.shape[0] - 1, h, dtype=int)
                    xi = np.linspace(0, f.shape[1] - 1, w, dtype=int)
                    f = f[yi][:, xi]
                return f.astype(np.uint8)

            # expose close() so the render loop can release the ffmpeg
            # subprocess immediately after the segment is written to disk
            make.close = vc.close
            return make

        base = _procedural_base(ref.query + ref.id, w, h)

        def make(local_t: float, base=base):
            # slow drift + breathing luminance => motion for camera/cut layers
            shift = int((local_t * 18) % base.shape[1])
            lum = 0.92 + 0.08 * np.sin(local_t * 1.3)
            frame = np.roll(base, shift, axis=1).astype(np.float32) * lum
            return np.clip(frame, 0, 255).astype(np.uint8)

        return make

    # -- public: render visual_source --------------------------------------
    def visual_source(self, seg: Segment, ctx) -> Callable[[float], np.ndarray]:
        w, h, dur = ctx.w, ctx.h, max(0.1, seg.duration)

        if seg.camera_motion == "rapid_clip_montage" and seg.montage_clips:
            spans, t0 = [], 0.0
            fns: list = []
            total_plan = sum(m.duration for m in seg.montage_clips) or dur
            for m in seg.montage_clips:
                seg_dur = dur * (m.duration / total_plan)
                ref = self._resolve(m.query, "", seg.beat_type, seg_dur)
                self.manifest.append({"segment": seg.id, "clip": ref.id,
                                      "source": ref.source, "query": ref.query})
                fn = self._make_frame_fn(ref, w, h, seg_dur)
                fns.append(fn)
                spans.append((t0, t0 + seg_dur, fn))
                t0 += seg_dur

            def make(local_t: float, spans=spans):
                for a, b, fn in spans:
                    if a <= local_t < b or b >= dur:
                        return fn(local_t - a)
                return spans[-1][2](local_t - spans[-1][0])

            def _close_montage(fns=fns):
                for fn in fns:
                    if hasattr(fn, "close"):
                        fn.close()

            make.close = _close_montage
            return make

        ref = self._resolve(seg.clip_query_primary, seg.clip_query_backup,
                            seg.beat_type, dur)
        self.manifest.append({"segment": seg.id, "clip": ref.id,
                              "source": ref.source, "query": ref.query})
        return self._make_frame_fn(ref, w, h, dur)
