"""Layer 4 (color grade) + Layer 9 (always-on film FX).

ColorGrade runs BEFORE text (grade the footage, not the captions).
FilmFX (grain / vignette / chromatic aberration) runs AFTER text for a
cohesive filmic finish; intensities come from the segment override or the
video_meta base value.

Real `.cube` LUTs (planning/04 global_assets.luts) are applied if the file
exists in assets/luts; otherwise a per-grade synthetic tone/channel/sat
transform approximates the named mood (assets ship empty by default).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

from ..config import ASSETS

# grade -> (channel gain RGB, saturation, lift, gamma)
_GRADES = {
    "cold_isolation": ((0.86, 0.97, 1.18), 0.72, 0.02, 1.05),
    "warm_comfort": ((1.18, 1.02, 0.82), 0.95, 0.03, 0.96),
    "warm_comfort_dark": ((1.12, 0.98, 0.80), 0.80, -0.02, 1.10),
    "clinical": ((0.88, 1.06, 1.10), 0.78, 0.02, 1.0),
    "surveillance": ((0.80, 1.0, 0.86), 0.55, -0.03, 1.12),
    "threat": ((1.30, 0.78, 0.78), 0.70, -0.02, 1.10),
    "interrogation": ((1.06, 1.04, 0.92), 0.65, 0.05, 0.92),
    "revelation": ((1.10, 1.08, 0.96), 1.05, 0.06, 0.88),
    "memory": ((1.04, 0.98, 1.02), 0.60, 0.04, 1.0),
    "madness": ((1.16, 0.82, 1.10), 0.85, -0.02, 1.08),
    "dream": ((1.02, 1.02, 1.12), 0.75, 0.08, 0.9),
    "death": ((0.95, 0.95, 0.95), 0.12, -0.02, 1.15),
    "nostalgia": ((1.14, 1.0, 0.84), 0.70, 0.05, 0.95),
}
_LUMA = np.array([0.299, 0.587, 0.114], np.float32)


@lru_cache(maxsize=16)
def _load_cube(path: str) -> tuple[np.ndarray, int] | None:
    try:
        size = 0
        rows = []
        for ln in Path(path).read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith(("#", "TITLE", "DOMAIN")):
                continue
            if ln.startswith("LUT_3D_SIZE"):
                size = int(ln.split()[-1])
                continue
            p = ln.split()
            if len(p) == 3:
                rows.append([float(x) for x in p])
        if size and len(rows) == size**3:
            return np.array(rows, np.float32).reshape(size, size, size, 3), size
    except Exception:
        pass
    return None


def _apply_cube(frame: np.ndarray, lut: np.ndarray, size: int) -> np.ndarray:
    idx = np.clip((frame / 255.0 * (size - 1)).round().astype(int), 0, size - 1)
    r, g, b = idx[..., 0], idx[..., 1], idx[..., 2]
    return np.clip(lut[r, g, b] * 255.0, 0, 255).astype(np.uint8)


class ColorGrade:
    def __call__(self, seg, frame: np.ndarray, local_t: float, ctx) -> np.ndarray:
        grade = seg.color_grade_override or ctx.doc.video_meta.get(
            "base_color_grade", "cold_isolation"
        )
        lut_rel = ctx.doc.global_assets.get("luts", {}).get(grade)
        if lut_rel:
            cube = _load_cube(str(ASSETS / lut_rel))
            if cube:
                return _apply_cube(frame, *cube)

        gain, sat, lift, gamma = _GRADES.get(grade, _GRADES["cold_isolation"])
        f = frame.astype(np.float32) / 255.0
        f = f * np.array(gain, np.float32)
        luma = (f * _LUMA).sum(axis=2, keepdims=True)
        f = luma + (f - luma) * sat            # saturation
        f = np.clip(f + lift, 0, 1) ** gamma   # lift + gamma
        return np.clip(f * 255.0, 0, 255).astype(np.uint8)


@lru_cache(maxsize=8)
def _vignette_mask(h: int, w: int) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w / 2.0, h / 2.0
    d = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2)
    return np.clip(d / 1.414, 0, 1).astype(np.float32)  # 0 center .. 1 corner


class FilmFX:
    """Grain + vignette + chromatic aberration; intensity per segment."""

    def __call__(self, seg, frame: np.ndarray, local_t: float, ctx) -> np.ndarray:
        meta = ctx.doc.video_meta
        grain = seg.grain_override if seg.grain_override is not None else meta.get("base_grain", 0.2)
        vig = seg.vignette_override if seg.vignette_override is not None else meta.get("base_vignette", 0.35)
        ca = seg.chromatic_aberration or meta.get("base_chromatic_aberration", 0.0)
        h, w = frame.shape[:2]
        f = frame.astype(np.float32)

        if ca > 0.001:
            shift = max(1, int(ca * 12))
            f[..., 0] = np.roll(f[..., 0], shift, axis=1)
            f[..., 2] = np.roll(f[..., 2], -shift, axis=1)

        if vig > 0.001:
            m = _vignette_mask(h, w)[..., None]
            f *= 1.0 - (m * float(np.clip(vig, 0, 0.85)))

        if grain > 0.001:
            n = np.random.default_rng(int(local_t * 1000) & 0xFFFF).normal(
                0, grain * 42.0, (h, w, 1)
            ).astype(np.float32)
            f += n

        return np.clip(f, 0, 255).astype(np.uint8)
