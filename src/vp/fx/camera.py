"""Layer 3 (camera motion) + Layer 5 (cut rhythm), as frame xforms.

Camera runs EARLY in the chain (before text, so captions stay locked on
top). Cut transitions run LAST (on the final composited frame) and live at
the head/tail of each segment — the renderer concatenates segments
back-to-back, so a "dip to black" out + "dip from black" in on adjacent
segments reads as the edit between them.
"""
from __future__ import annotations

import cv2
import numpy as np

# default scale envelope when JSON omits camera_scale_start/end
_MOTION_SCALE = {
    "slow_push_in": (1.0, 1.06), "aggressive_push_in": (1.0, 1.16),
    "crash_zoom": (1.0, 1.22), "zoom_punch": (1.0, 1.12),
    "slow_pull_out": (1.12, 1.0), "dolly_zoom": (1.04, 1.14),
}


def _progress(local_t: float, dur: float) -> float:
    return float(np.clip(local_t / max(0.1, dur), 0.0, 1.0))


class CameraMotion:
    """Apply scale / translate / rotate per segment over its duration."""

    def __call__(self, seg, frame: np.ndarray, local_t: float, ctx) -> np.ndarray:
        m = seg.camera_motion
        if m in ("locked_frame", "rapid_clip_montage"):
            return frame
        h, w = frame.shape[:2]
        dur = max(0.1, seg.duration)
        p = _progress(local_t, dur)

        s0, s1 = _MOTION_SCALE.get(m, (None, None))
        if s0 is None:
            s0 = seg.camera_scale_start or 1.0
            s1 = seg.camera_scale_end or 1.0
        if m == "crash_zoom":  # ease-in punch
            p = p * p
        scale = s0 + (s1 - s0) * p
        scale = max(1.0, scale)  # never below 1 -> no empty borders

        tx = ty = 0.0
        ang = 0.0
        if m == "imperceptible_drift":
            tx = 1.5 * local_t
            scale = max(scale, 1.03)
        elif m == "subtle_drift_right":
            tx = 14 * p; scale = max(scale, 1.05)
        elif m == "subtle_drift_left":
            tx = -14 * p; scale = max(scale, 1.05)
        elif m in ("light_shake", "heavy_shake", "breath_rhythm_shake",
                   "recoil_on_impact"):
            amp = {"light_shake": 3, "heavy_shake": 9,
                   "breath_rhythm_shake": 4, "recoil_on_impact": 12}[m]
            tx = amp * np.sin(local_t * 37.0)
            ty = amp * np.cos(local_t * 29.0)
            scale = max(scale, 1.04)
        elif m == "dutch_angle_tilt":
            ang = 4.0; scale = max(scale, 1.08)
        elif m in ("spin_transition", "mirror_flip"):
            ang = 360.0 * p if m == "spin_transition" else 0.0
            if m == "mirror_flip" and p > 0.5:
                frame = frame[:, ::-1]
            scale = max(scale, 1.05)
        elif m == "whip_pan":
            tx = (w * 0.5) * (1 - abs(2 * p - 1))
            scale = max(scale, 1.05)
        elif m == "tilt_up_reveal":
            ty = 40 * (1 - p); scale = max(scale, 1.06)
        elif m == "crane_down":
            ty = -40 * (1 - p); scale = max(scale, 1.06)

        cx, cy = w / 2.0, h / 2.0
        M = cv2.getRotationMatrix2D((cx, cy), ang, scale)
        M[0, 2] += tx
        M[1, 2] += ty
        return cv2.warpAffine(frame, M, (w, h), borderMode=cv2.BORDER_REFLECT)


class CutRhythm:
    """Head/tail transition for cut_in_type / cut_out_type."""

    EDGE = 0.16  # seconds of transition

    def _factor(self, kind: str, x: float) -> tuple[float, int]:
        """x in [0,1] across the edge. -> (luma_mult, flash_white?)."""
        if kind in ("hard_cut", "match_cut", "j_cut", "l_cut",
                    "hold_then_hard_cut"):
            return 1.0, 0
        if kind in ("dip_to_black", "dip_from_black", "dip_to_black_brief",
                    "smash_cut", "glitch_transition"):
            return x, 0
        if kind in ("flash_frame_white", "flash_frame_black"):
            return (1.0, 1) if x < 0.5 and kind.endswith("white") else (x, 0)
        if kind in ("cross_dissolve", "cross_dissolve_brief",
                    "whip_pan_transition"):
            return x, 0
        return 1.0, 0

    def __call__(self, seg, frame: np.ndarray, local_t: float, ctx) -> np.ndarray:
        dur = max(0.1, seg.duration)
        mult, flash = 1.0, 0
        if local_t < self.EDGE:  # cut-IN
            x = local_t / self.EDGE
            mult, flash = self._factor(seg.cut_in_type, x)
        elif local_t > dur - self.EDGE:  # cut-OUT
            x = (dur - local_t) / self.EDGE
            mult, flash = self._factor(seg.cut_out_type, x)
        if flash:
            return np.full_like(frame, 255)
        if mult >= 0.999:
            return frame
        return (frame.astype(np.float32) * mult).astype(np.uint8)
