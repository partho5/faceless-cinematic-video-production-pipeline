"""Metadata stage (G2) — the biggest revenue lever.

- Thumbnail 1280x720: pick the most visually interesting source frame
  (frame_compose mode, planning/10) + a punchy Pillow text overlay.
- Title variants / description / tags / chapters via the `metadata_text`
  model (lazy Anthropic), with a deterministic offline fallback.
- Always includes the synthetic-media disclosure line (G11).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ..config import Config
from ..fx.fonts import load_font
from ..schema.model import ControlDocument

_STOP = set("the a an of to is are be in on for you your they them it that this "
            "and or but not with as at by from was were has have had will".split())
DISCLOSURE = ("This video uses an AI-generated voice and automated assembly "
              "(synthetic/altered media).")


def _best_frame(video: Path, n: int = 12) -> Image.Image:
    """Sample n frames; keep the one with the most luminance variance."""
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)], capture_output=True, text=True
    ).stdout.strip() or 6.0)
    best, best_score = None, -1.0
    tmp = video.parent / "_thumb_src.png"
    for i in range(1, n + 1):
        t = dur * i / (n + 1)
        subprocess.run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(video),
                        "-vframes", "1", str(tmp)], capture_output=True)
        if not tmp.exists():
            continue
        im = Image.open(tmp).convert("RGB")
        score = float(np.asarray(im.convert("L")).std())
        if score > best_score:
            best, best_score = im.copy(), score
    tmp.unlink(missing_ok=True)
    return best or Image.new("RGB", (1280, 720), (20, 20, 28))


def _overlay_words(title: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9]+", title) if w.lower() not in _STOP]
    return " ".join(words[:3]).upper() if words else "WATCH THIS"


def compose_thumbnail(frame: Image.Image, title: str, out: Path) -> Path:
    im = frame.resize((1280, 720), Image.LANCZOS).convert("RGB")
    # cinematic darken + bottom gradient for text legibility
    arr = (np.asarray(im).astype(np.float32) * 0.72)
    grad = np.linspace(1.0, 0.35, 720, dtype=np.float32)[:, None, None]
    arr *= grad
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    text = _overlay_words(title)
    font = load_font("aggressive", None, 132)
    bbox = d.textbbox((0, 0), text, font=font, stroke_width=6)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x, y = (1280 - tw) // 2, 720 - th - 90
    d.rectangle([0, y - 30, 1280, y + th + 40], fill=(0, 0, 0))
    d.text((x, y), text, font=font, fill=(232, 69, 69),
           stroke_width=6, stroke_fill=(0, 0, 0))
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "JPEG", quality=90)
    return out


def _chapters_timestamps(doc: ControlDocument) -> list[str]:
    out = []
    for c in sorted(doc.chapters, key=lambda c: c.start):
        m, s = divmod(int(c.start), 60)
        name = c.chapter_id.replace("_", " ").title()
        out.append(f"{m:01d}:{s:02d} {name}")
    return out


class MetadataStage:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.spec = cfg.model("metadata_text")

    def _text_offline(self, title: str, script: str) -> dict:
        base = title.rstrip(".")
        variants = [
            base,
            f"{base} (Most People Miss #7)",
            f"Watch For These: {base}",
        ]
        words = [w.lower() for w in re.findall(r"[A-Za-z]+", script)]
        freq: dict[str, int] = {}
        for w in words:
            if w not in _STOP and len(w) > 3:
                freq[w] = freq.get(w, 0) + 1
        tags = [k for k, _ in sorted(freq.items(), key=lambda x: -x[1])[:15]]
        desc = (f"{base}.\n\n" + script.strip().split("\n\n")[0][:280]
                + "\n\n" + DISCLOSURE)
        return {"title_variants": variants, "description": desc, "tags": tags}

    def _text_live(self, title: str, script: str) -> dict:
        from ..llm import anthropic_message  # lazy

        raw = anthropic_message(
            self.spec,
            system="Return JSON {title_variants:[3],description,tags:[]} "
                   "for a dark-psychology YouTube video.",
            user=f"Title: {title}\nScript:\n{script[:4000]}",
        )
        data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        data["description"] = data.get("description", "") + "\n\n" + DISCLOSURE
        return data

    def run(self, video_path: Path, doc: ControlDocument, script: str,
            out_dir: Path) -> dict:
        title = doc.video_meta.get("title", "Untitled")
        thumb = compose_thumbnail(_best_frame(video_path), title,
                                  out_dir / "thumbnail.jpg")
        if self.spec.offline:
            text = self._text_offline(title, script)
        else:
            try:
                text = self._text_live(title, script)
            except Exception:
                text = self._text_offline(title, script)
        meta = {
            "title": text["title_variants"][0],
            "title_variants": text["title_variants"],
            "description": text["description"],
            "tags": text["tags"],
            "chapters": _chapters_timestamps(doc),
            "thumbnail": str(thumb),
            "synthetic_media_disclosure": True,  # G11 / YouTube altered-content
            "category": "Education",
        }
        (out_dir / "metadata.json").write_text(json.dumps(meta, indent=2),
                                               encoding="utf-8")
        return meta
