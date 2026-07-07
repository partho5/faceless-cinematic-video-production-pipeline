"""Layer 2 + Layer 8: word-timed stylized caption overlay.

Registered as a frame xform. Per segment it lays the `text_overlay` out in
the configured typography personality and reveals/animates it on the
forced-aligned word times (ctx.alignments[seg.id]); emphasis effects fire on
the exact spoken word span; an exit animation plays at the tail.

`local_t` (render time within segment) shares the same clock as the aligned
word times (offsets relative to the segment audio file), so they compare
directly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from .fonts import load_font, style_for


@dataclass
class _Word:
    text: str
    start: float
    end: float
    x: int
    y: int
    w: int
    h: int
    emphasis: str | None = None
    color_shift: str | None = None


def _hex(c: str, default=(255, 255, 255)) -> tuple[int, int, int]:
    try:
        c = c.lstrip("#")
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    except Exception:
        return default


class TextRenderer:
    def __init__(self) -> None:
        self._cache: dict[str, list[_Word]] = {}

    # -- layout (once per segment) -----------------------------------------
    def _layout(self, seg, ctx) -> list[_Word]:
        if seg.id in self._cache:
            return self._cache[seg.id]

        style = style_for(seg.text_personality)
        raw = seg.text_overlay.upper() if style.get("caps") else seg.text_overlay
        tokens = [t for t in raw.split() if t]

        fonts_key = ctx.doc.global_assets.get("fonts", {}).get(seg.text_personality)
        size = max(20, int(ctx.h * 0.072))
        font = load_font(seg.text_personality, fonts_key, size)

        # word timings from alignment (fallback: proportional)
        al = ctx.alignments.get(seg.id)
        dur = max(0.1, seg.duration)
        if al and len(al.words) == len(tokens):
            times = [(w.start, w.end) for w in al.words]
        else:
            step = dur / max(1, len(tokens))
            times = [(i * step, (i + 1) * step) for i in range(len(tokens))]

        # emphasis lookup by aligned phrase span
        emph_spans = []
        for e in seg.text_animation_emphasis:
            span = al.find(e.word) if al else None
            emph_spans.append((e.word, e.effect, e.color_shift, span))

        # word-wrap to <=86% width
        max_w = int(ctx.w * 0.86)
        tmp = Image.new("RGBA", (10, 10))
        d = ImageDraw.Draw(tmp)
        space_w = d.textlength(" ", font=font) + style.get("tracking", 0)

        lines: list[list[tuple[str, int, float, float]]] = [[]]
        cur_w = 0
        for tok, (ts, te) in zip(tokens, times):
            bbox = d.textbbox((0, 0), tok, font=font)
            tw = bbox[2] - bbox[0]
            if cur_w + tw > max_w and lines[-1]:
                lines.append([])
                cur_w = 0
            lines[-1].append((tok, tw, ts, te))
            cur_w += tw + space_w
        line_h = int(size * 1.25)
        block_h = line_h * len(lines)

        pos = seg.text_position
        if pos == "bottom_third":
            y0 = int(ctx.h * 0.74) - block_h // 2
        elif pos == "top_third":
            y0 = int(ctx.h * 0.24)
        else:
            y0 = (ctx.h - block_h) // 2

        words: list[_Word] = []
        for li, line in enumerate(lines):
            line_w = sum(w for _, w, _, _ in line) + space_w * (len(line) - 1)
            x = (ctx.w - int(line_w)) // 2
            y = y0 + li * line_h
            for tok, tw, ts, te in line:
                em, cs = None, None
                tl = tok.lower().strip(".,!?;:")
                for eword, eff, csh, span in emph_spans:
                    if tl in eword.lower().split():
                        em, cs = eff, csh
                words.append(_Word(tok, ts, te, x, y, int(tw), line_h, em, cs))
                x += int(tw) + int(space_w)

        self._cache[seg.id] = words
        return words

    # -- per-frame ---------------------------------------------------------
    def __call__(self, seg, frame: np.ndarray, local_t: float, ctx) -> np.ndarray:
        if not seg.text_overlay.strip():
            return frame
        words = self._layout(seg, ctx)
        style = style_for(seg.text_personality)
        base_rgb = _hex(seg.text_color)
        dur = max(0.1, seg.duration)
        anim_in = seg.text_animation_in
        anim_out = seg.text_animation_out

        overlay = Image.new("RGBA", (ctx.w, ctx.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        fonts_key = ctx.doc.global_assets.get("fonts", {}).get(seg.text_personality)
        size = max(20, int(ctx.h * 0.072))
        font = load_font(seg.text_personality, fonts_key, size)

        # exit fade (last 0.4s) — covers fade/flash/shrink/hard variants
        out_a = 1.0
        out_dx = out_dy = 0
        if local_t > dur - 0.4:
            p = (local_t - (dur - 0.4)) / 0.4
            if anim_out in ("hard_cut", "hold_then_hard_cut"):
                out_a = 1.0
            elif anim_out == "flash_white_gone":
                out_a = 1.0 if p < 0.5 else 0.0
            elif anim_out == "shrink_to_dot":
                out_a = 1.0 - p
            else:  # fade/dissolve/shatter/flash_to_dissolve/freeze_then_glitch
                out_a = max(0.0, 1.0 - p)

        any_emph_active = any(
            (w.emphasis and w.start <= local_t <= w.end + 0.25) for w in words
        )

        for i, w in enumerate(words):
            # ---- reveal progress (in-animation) --------------------------
            a, dx, dy, scale = 1.0, 0, 0, 1.0
            lead = 0.18
            if anim_in in ("word_by_word_fade", "words_emerge_from_blur",
                           "fade_from_blur", "split_text_reveal"):
                a = float(np.clip((local_t - (w.start - lead)) / lead, 0, 1))
            elif anim_in in ("typewriter_character", "staggered_line_drop"):
                a = 1.0 if local_t >= w.start - 0.02 else 0.0
                if anim_in == "staggered_line_drop":
                    dy = int(-30 * (1 - np.clip(
                        (local_t - (w.start - lead)) / lead, 0, 1)))
            elif anim_in in ("word_slam_random_direction",
                             "split_character_reveal"):
                p = float(np.clip((local_t - (w.start - lead)) / lead, 0, 1))
                a = p
                ang = (hash(w.text) % 360) * math.pi / 180
                dx = int((1 - p) * 60 * math.cos(ang))
                dy = int((1 - p) * 60 * math.sin(ang))
            else:
                a = float(np.clip((local_t - (w.start - lead)) / lead, 0, 1))
            if a <= 0:
                continue

            color = base_rgb
            # ---- emphasis -------------------------------------------------
            if w.emphasis and w.start <= local_t <= w.end + 0.25:
                eff = w.emphasis
                if w.color_shift:
                    color = _hex(w.color_shift, base_rgb)
                if "scale_up" in eff:
                    scale = 1.28
                if "red" in eff:
                    color = (232, 69, 69)
                if "glow_white" in eff or eff == "scale_up_glow":
                    color = (255, 255, 255)
                if "shake" in eff:
                    dx += int(6 * math.sin(local_t * 60))
                    dy += int(4 * math.cos(local_t * 70))
                if "glitch" in eff:
                    dx += int(8 * (np.random.rand() - 0.5))
                if "ember" in eff:
                    color = (255, 120, 60)
            # highlight_*_dim_rest: non-emphasis words dim while an emphasis
            # word is active
            word_a = a * out_a
            if any_emph_active and not (
                w.emphasis and w.start <= local_t <= w.end + 0.25
            ):
                if any("dim_rest" in (ww.emphasis or "") for ww in words):
                    word_a *= 0.32

            word_a *= style.get("alpha", 1.0)
            alpha = int(255 * float(np.clip(word_a, 0, 1)))
            if alpha <= 2:
                continue

            fs = font
            if scale != 1.0:
                fs = load_font(seg.text_personality, fonts_key,
                               int(size * scale))
            x = w.x + dx + out_dx
            y = w.y + dy + out_dy
            font_size = int(size * scale)
            stroke = max(4, font_size // 12)
            d.text((x, y), w.text, font=fs, fill=color + (alpha,),
                   stroke_width=stroke, stroke_fill=(0, 0, 0, alpha))

        ov = np.asarray(overlay).astype(np.float32)
        a = ov[..., 3:4] / 255.0
        out = frame.astype(np.float32) * (1 - a) + ov[..., :3] * a
        return np.clip(out, 0, 255).astype(np.uint8)
