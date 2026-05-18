"""Resolve a typography personality -> a usable TTF.

Priority: configured font in assets/fonts (planning/04 global_assets.fonts)
-> a discovered system TTF -> PIL bundled default. Never raises: text must
always render so QA never sees a blank caption.
"""
from __future__ import annotations

import glob
import sys
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from ..config import ASSETS

# Bold-ish sans first (punchy captions), per OS. The Linux list/glob is
# unchanged so existing Linux renders stay byte-identical; Windows/macOS
# entries make captions render properly there instead of falling back to
# PIL's tiny bitmap default.
_SYS_CANDIDATES = {
    "linux": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ],
    "win32": [
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
    ],
    "darwin": [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ],
}
_GLOB_ROOTS = {
    "linux": "/usr/share/fonts/**/*.ttf",
    "win32": r"C:\Windows\Fonts\*.ttf",
    "darwin": "/System/Library/Fonts/**/*.tt[fc]",
}


def _plat() -> str:
    if sys.platform.startswith("win"):
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"

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
    p = _plat()
    for c in _SYS_CANDIDATES.get(p, _SYS_CANDIDATES["linux"]):
        if Path(c).exists():
            return c
    hits = glob.glob(_GLOB_ROOTS.get(p, _GLOB_ROOTS["linux"]), recursive=True)
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
