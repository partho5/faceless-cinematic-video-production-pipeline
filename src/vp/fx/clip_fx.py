"""Clip-level visual effects (anti-fatigue system).

Each public function is an EFFECT FACTORY — call it to get a configured
FrameXform that can be dropped into the render pipeline.

To add a new effect later:
  1. Write a factory function here.
  2. Append it to EFFECT_REGISTRY at the bottom.
  That's it. The render engine picks randomly from the registry.

FrameXform signature (same as render.py):
    (segment, frame[H,W,3 uint8], local_t, ctx) -> frame[H,W,3 uint8]

All effects operate ONLY on the clip frame — subtitles are composited
on top afterwards by TextRenderer, so they are never affected here.
"""
from __future__ import annotations

from typing import Callable

import cv2
import numpy as np

# Type alias kept consistent with render.py
FrameXform = Callable  # (seg, frame, local_t, ctx) -> frame


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ease_out(p: float) -> float:
    """Smooth deceleration curve: fast start, gentle finish."""
    return 1.0 - (1.0 - p) ** 2


def _scale_frame(frame: np.ndarray, scale: float, cx: float, cy: float) -> np.ndarray:
    """Zoom frame around (cx, cy) using warpAffine (same as CameraMotion)."""
    h, w = frame.shape[:2]
    M = cv2.getRotationMatrix2D((cx, cy), 0.0, scale)
    return cv2.warpAffine(frame, M, (w, h), borderMode=cv2.BORDER_REFLECT)


def _progress(local_t: float, duration: float) -> float:
    """Linear progress [0, 1] clamped to the effect window."""
    return float(np.clip(local_t / max(0.01, duration), 0.0, 1.0))


# ─────────────────────────────────────────────────────────────────────────────
# Effect #1a — Zoom In
# ─────────────────────────────────────────────────────────────────────────────

def zoom_in(duration: float = 0.35, scale_start: float = 1.0, scale_end: float = 1.22) -> FrameXform:
    """Fast zoom INTO the clip at its start.

    Args:
        duration:    Seconds over which the zoom ramps (0.1 = snap, 0.5 = dramatic).
        scale_start: Initial scale factor (1.0 = normal).
        scale_end:   Peak scale factor reached at `duration`.
    """
    def _xform(seg, frame: np.ndarray, local_t: float, ctx) -> np.ndarray:
        if local_t >= duration:
            # Hold at final scale for the rest of the clip
            h, w = frame.shape[:2]
            return _scale_frame(frame, scale_end, w / 2.0, h / 2.0)
        p = _ease_out(_progress(local_t, duration))
        scale = scale_start + (scale_end - scale_start) * p
        h, w = frame.shape[:2]
        return _scale_frame(frame, scale, w / 2.0, h / 2.0)
    return _xform


# ─────────────────────────────────────────────────────────────────────────────
# Effect #1b — Zoom Out
# ─────────────────────────────────────────────────────────────────────────────

def zoom_out(duration: float = 0.35, scale_start: float = 1.22, scale_end: float = 1.0) -> FrameXform:
    """Fast pull-back zoom at the clip start — starts close, pulls to normal.

    Args:
        duration:    Seconds over which the zoom ramps.
        scale_start: Initial (zoomed-in) scale.
        scale_end:   Final scale (1.0 = normal frame).
    """
    def _xform(seg, frame: np.ndarray, local_t: float, ctx) -> np.ndarray:
        if local_t >= duration:
            return frame  # normal scale for the rest of the clip
        p = _ease_out(_progress(local_t, duration))
        scale = scale_start + (scale_end - scale_start) * p
        h, w = frame.shape[:2]
        return _scale_frame(frame, scale, w / 2.0, h / 2.0)
    return _xform


# ─────────────────────────────────────────────────────────────────────────────
# Effect #2 — Fade In
# ─────────────────────────────────────────────────────────────────────────────

def fade_in(duration: float = 0.25) -> FrameXform:
    """Clip fades in from black over `duration` seconds.

    Args:
        duration: Length of the fade (0.1 = very snappy, 0.5 = cinematic).
    """
    def _xform(seg, frame: np.ndarray, local_t: float, ctx) -> np.ndarray:
        if local_t >= duration:
            return frame
        alpha = _ease_out(_progress(local_t, duration))
        return (frame.astype(np.float32) * alpha).astype(np.uint8)
    return _xform


# ─────────────────────────────────────────────────────────────────────────────
# Effect #3 — Slide In from Left
# ─────────────────────────────────────────────────────────────────────────────

def slide_from_left(duration: float = 0.30) -> FrameXform:
    """Clip slides in from the left edge over `duration` seconds."""
    def _xform(seg, frame: np.ndarray, local_t: float, ctx) -> np.ndarray:
        if local_t >= duration:
            return frame
        p = _ease_out(_progress(local_t, duration))
        h, w = frame.shape[:2]
        shift = int(w * (1.0 - p))  # starts at full-width offset, shrinks to 0
        M = np.float32([[1, 0, shift], [0, 1, 0]])
        return cv2.warpAffine(frame, M, (w, h), borderMode=cv2.BORDER_REFLECT)
    return _xform


# ─────────────────────────────────────────────────────────────────────────────
# Effect #4 — Slide In from Right
# ─────────────────────────────────────────────────────────────────────────────

def slide_from_right(duration: float = 0.30) -> FrameXform:
    """Clip slides in from the right edge over `duration` seconds."""
    def _xform(seg, frame: np.ndarray, local_t: float, ctx) -> np.ndarray:
        if local_t >= duration:
            return frame
        p = _ease_out(_progress(local_t, duration))
        h, w = frame.shape[:2]
        shift = int(-w * (1.0 - p))  # starts at negative offset, moves to 0
        M = np.float32([[1, 0, shift], [0, 1, 0]])
        return cv2.warpAffine(frame, M, (w, h), borderMode=cv2.BORDER_REFLECT)
    return _xform


# ─────────────────────────────────────────────────────────────────────────────
# Effect #5 — Slide In from Bottom
# ─────────────────────────────────────────────────────────────────────────────

def slide_from_bottom(duration: float = 0.30) -> FrameXform:
    """Clip slides up from below over `duration` seconds."""
    def _xform(seg, frame: np.ndarray, local_t: float, ctx) -> np.ndarray:
        if local_t >= duration:
            return frame
        p = _ease_out(_progress(local_t, duration))
        h, w = frame.shape[:2]
        shift = int(h * (1.0 - p))
        M = np.float32([[1, 0, 0], [0, 1, shift]])
        return cv2.warpAffine(frame, M, (w, h), borderMode=cv2.BORDER_REFLECT)
    return _xform


# ─────────────────────────────────────────────────────────────────────────────
# EFFECT REGISTRY
# ─────────────────────────────────────────────────────────────────────────────
# The render engine randomly picks one entry per clip.
# To add a new effect: define it above, then append its factory here.
# No other changes needed anywhere.

EFFECT_REGISTRY: list[Callable] = [
    zoom_in,
    zoom_out,
    fade_in,
    slide_from_left,
    slide_from_right,
    slide_from_bottom,
]
