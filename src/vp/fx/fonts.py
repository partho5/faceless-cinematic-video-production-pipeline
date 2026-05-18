"""Resolve a typography personality -> a usable TTF.

Priority: configured font in assets/fonts (planning/04 global_assets.fonts)
-> a discovered system TTF -> PIL bundled default. Never raises: text must
always render so QA never sees a blank caption.
"""
from __future__ import annotations

import glob
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from ..config import ASSETS

_SYS_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

# personality -> stylistic hints consumed by the text renderer
PERSONALITY_STYLE = {
    "aggressive": {"caps": True, "skew": 0.12, "stroke": 3, "tracking": 2},
    "clinical": {"caps": False, "skew": 0.0, "stroke": 1, "tracking": 0},
    "whisper": {"caps": False, "skew": 0.0, "stroke": 0, "tracking": 1,
                "glow": True, "alpha": 0.85},
    "reveal": {"caps": False, "skew": 0.0, "stroke": 2, "tracking": 1},
    "handwritten": {"caps": False, "skew": -0.06, "stroke": 1, "tracking": 0},
}


@lru_cache(maxsize=64)
def _system_font_path() -> str | None:
    for c in _SYS_CANDIDATES:
        if Path(c).exists():
            return c
    hits = glob.glob("/usr/share/fonts/**/*.ttf", recursive=True)
    return hits[0] if hits else None


@lru_cache(maxsize=128)
def load_font(personality: str, fonts_map_key: str | None, size: int) -> ImageFont.FreeTypeFont:
    candidates: list[str] = []
    if fonts_map_key:
        candidates.append(str(ASSETS / "fonts" / fonts_map_key))
    sysf = _system_font_path()
    if sysf:
        candidates.append(sysf)
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    return ImageFont.load_default()


def style_for(personality: str) -> dict:
    return PERSONALITY_STYLE.get(personality, PERSONALITY_STYLE["clinical"])
