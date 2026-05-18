"""Layer 3 (always-on Ken Burns) + Layer 9 (ambient texture) + Layer 10
(whole-video arc) — additive frame xforms.

Design rule: STABILITY-FIRST. These never reorder or modify the existing
chain. They are appended in the run scripts and:

  * KenBurns runs FIRST (on raw footage). It yields entirely — returns the
    frame untouched — for any segment that already carries an explicit
    camera move, so those segments stay byte-identical to the old pipeline.
    Only static / drift / montage clips (which were lifeless before) gain a
    slow, deterministic pan-zoom so every second has motion.

  * FilmAmbience runs LAST-ish (after FilmFX, before CutRhythm). It is
    GATED by mood/beat: for the common cold_isolation / threat / clinical
    grades it returns the frame unchanged (zero cost, zero visual change),
    so existing typical videos look essentially the same. Light leaks,
    dust and scan lines only appear on the moods/beats that ask for them.

A gentle Layer-10 arc (opening hotter, outro calmer) modulates intensity
using the segment's position in the document — no schema change.
"""
from __future__ import annotations

import zlib

import cv2
import numpy as np

# camera moves that already animate the frame -> Ken Burns must defer
_EXPLICIT_MOTION = {
    "slow_push_in", "aggressive_push_in", "crash_zoom", "zoom_punch",
    "slow_pull_out", "dolly_zoom", "whip_pan", "spin_transition",
    "tilt_up_reveal", "crane_down", "rapid_clip_montage",
}


def _seed(s: str) -> int:
    return zlib.crc32(s.encode("utf-8")) & 0xFFFFFFFF


def _smooth(p: float) -> float:
    p = float(np.clip(p, 0.0, 1.0))
    return p * p * (3.0 - 2.0 * p)


def _seg_progress(seg, ctx) -> float:
    """0.0 at first segment .. 1.0 at last — for the Layer-10 arc."""
    segs = ctx.doc.segments
    try:
        i = next(k for k, s in enumerate(segs) if s.id == seg.id)
    except StopIteration:
        return 0.5
    return i / max(1, len(segs) - 1)


class KenBurns:
    """Slow, deterministic pan + zoom on otherwise-static footage.

    Guarantees: output shape/dtype unchanged; scale kept >= 1 with
    BORDER_REFLECT so no black borders are ever introduced; fully bypassed
    (identity) when the segment already has an explicit camera move.
    """

    #: base zoom floor — gives pan headroom while staying subtle
    Z_FLOOR = 1.06
    Z_SWING = 0.05          # extra zoom traversed across the segment
    PAN_FRAC = 0.022        # max pan as a fraction of width/height

    def __call__(self, seg, frame: np.ndarray, local_t: float, ctx) -> np.ndarray:
        if seg.camera_motion in _EXPLICIT_MOTION:
            return frame  # defer entirely -> byte-identical to old pipeline
        h, w = frame.shape[:2]
        dur = max(0.1, seg.duration)
        p = _smooth(local_t / dur)

        sd = _seed(seg.id)
        zoom_in = bool(sd & 1)
        ang = (sd % 360) * np.pi / 180.0

        # Layer-10 arc: opening 20% a touch livelier, final 20% calmer.
        arc = _seg_progress(seg, ctx)
        energy = 1.0
        if arc < 0.2:
            energy = 1.15
        elif arc > 0.8:
            energy = 0.7

        swing = self.Z_SWING * energy
        z = self.Z_FLOOR + swing * (p if zoom_in else (1.0 - p))

        pan = self.PAN_FRAC * energy
        # ease the pan along a fixed direction across the whole segment
        tx = np.cos(ang) * pan * w * (p - 0.5) * 2.0
        ty = np.sin(ang) * pan * h * (p - 0.5) * 2.0

        # clamp pan so the zoomed crop never exposes an edge
        max_tx = (z - 1.0) * w * 0.5
        max_ty = (z - 1.0) * h * 0.5
        tx = float(np.clip(tx, -max_tx, max_tx))
        ty = float(np.clip(ty, -max_ty, max_ty))

        M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), 0.0, float(z))
        M[0, 2] += tx
        M[1, 2] += ty
        return cv2.warpAffine(frame, M, (w, h), borderMode=cv2.BORDER_REFLECT)


# moods that benefit from extra ambient texture; everything else -> identity
_LEAK_BEATS = {"reveal", "shock", "climax", "confession"}
_DUST_GRADES = {"memory", "dream", "nostalgia"}
_SCAN_GRADES = {"surveillance"}


def _grade(seg, ctx) -> str:
    return seg.color_grade_override or ctx.doc.video_meta.get(
        "base_color_grade", "cold_isolation"
    )


class FilmAmbience:
    """Layer 9 extras: light leaks, dust, scan lines — gated by mood/beat.

    Returns the frame UNCHANGED unless the segment's grade/beat opts in, so
    common grades render exactly as before (stability) while memory/dream/
    surveillance/reveal moments gain texture.
    """

    def __call__(self, seg, frame: np.ndarray, local_t: float, ctx) -> np.ndarray:
        grade = _grade(seg, ctx)
        beat = getattr(seg, "beat_type", "") or ""
        do_leak = beat in _LEAK_BEATS or grade == "revelation"
        do_dust = grade in _DUST_GRADES
        do_scan = grade in _SCAN_GRADES
        if not (do_leak or do_dust or do_scan):
            return frame  # zero-cost identity for the common path

        h, w = frame.shape[:2]
        f = frame.astype(np.float32)
        dur = max(0.1, seg.duration)
        p = local_t / dur

        if do_leak:
            # soft warm radial bloom drifting in from a deterministic corner,
            # pulsing once across the segment -> reads as a film light leak.
            sd = _seed(seg.id + "leak")
            cx = (0.12 if sd & 1 else 0.88) * w
            cy = (0.15 if sd & 2 else 0.85) * h
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            r = np.sqrt(((xx - cx) / w) ** 2 + ((yy - cy) / h) ** 2)
            pulse = np.sin(np.clip(p, 0, 1) * np.pi) ** 2  # 0->1->0
            glow = np.clip(1.0 - r * 2.2, 0.0, 1.0) ** 2 * (0.45 * pulse)
            tint = np.array([255, 196, 140], np.float32)
            f = f + glow[..., None] * tint  # screen-ish additive bloom

        if do_dust:
            # sparse drifting motes (deterministic field, slow vertical fall)
            rng = np.random.default_rng(_seed(seg.id + "dust"))
            n = 90
            px = (rng.random(n) * w).astype(np.int32)
            base_y = rng.random(n)
            fall = (base_y + local_t * 0.04) % 1.0
            py = (fall * h).astype(np.int32)
            br = (rng.random(n) * 70 + 40).astype(np.float32)
            for k in range(n):
                y0, x0 = py[k], px[k]
                if 1 <= y0 < h - 1 and 1 <= x0 < w - 1:
                    f[y0 - 1:y0 + 1, x0 - 1:x0 + 1] += br[k]

        if do_scan:
            # faint static horizontal CRT lines + slow roll
            roll = int((local_t * 30) % 4)
            f[roll::4, :, :] *= 0.90

        return np.clip(f, 0, 255).astype(np.uint8)
