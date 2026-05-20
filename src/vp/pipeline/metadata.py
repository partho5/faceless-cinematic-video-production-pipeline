"""Metadata stage (G2) — the biggest revenue lever.

- Thumbnail 1280x720: pick the most visually interesting source frame
  (frame_compose mode, planning/10) + a punchy Pillow text overlay.
- SEO-optimized title variants / description / tags / hashtags / keywords /
  chapters via the `metadata_text` model (lazy Anthropic), with a
  deterministic offline fallback.
- Composes a publish-ready description (hook → body → chapters → music
  credit → disclosure → hashtags) so metadata.json is copy-paste-ready
  for YouTube Studio whether or not auto-upload is enabled.
- Always includes the synthetic-media disclosure line (G11) and the
  CC BY 4.0 music attribution when a track is used.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ..config import ASSETS, Config
from ..fx.fonts import load_font
from ..schema.model import ControlDocument

_STOP = set("the a an of to is are be in on for you your they them it that this "
            "and or but not with as at by from was were has have had will".split())
DISCLOSURE = ("This video uses an AI-generated voice and automated assembly "
              "(synthetic/altered media).")
_CATALOG_PATH = ASSETS / "music" / "catalog.json"


def _load_music_attribution(music_design: dict | None) -> str | None:
    """Return the attribution string for the chosen music track, or None.

    None means: no music was used (track skipped or --no-music), the slug
    is missing from the catalog, or the track has `attribution: null`
    (no credit required). Failures are swallowed so a malformed catalog
    never blocks publishing.
    """
    if not music_design:
        return None
    slug = music_design.get("track")
    if not slug:
        return None
    try:
        catalog = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    for t in catalog.get("tracks", []):
        if t.get("file", "").removesuffix(".mp3") == slug:
            attr = t.get("attribution")
            return attr if isinstance(attr, str) and attr.strip() else None
    return None


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


_SEO_SYSTEM = """You write SEO-optimized YouTube metadata for a faceless \
dark-psychology / human-behavior channel. Return ONLY valid JSON, no prose.

Required keys:
  title_variants : array of 3 titles. Each ≤ 100 chars, hook-driven, \
curiosity-gap. Avoid clickbait punctuation like "!!!" or ALL CAPS words.
  description_body : 1200–1800 chars. Structure: (1) a 1–2 line hook \
that reuses the strongest title keywords; (2) a 4–6 line value-prop \
paragraph naturally weaving in 4–6 long-tail keywords from the script; \
(3) a short "What you'll learn" list of 3–5 bullets prefixed with "• "; \
(4) a 1-line CTA inviting like + subscribe + comment. Do NOT include \
chapter timestamps, music credits, hashtags, or the disclosure line — \
those are appended by the caller. Plain text only, no markdown.
  tags : array of 20–30 strings. Mix of broad (e.g. "psychology", \
"human behavior") and long-tail (e.g. "signs of covert manipulation"). \
Each tag ≤ 60 chars. No "#" prefix.
  hashtags : array of 3–5 short hashtags WITHOUT spaces, lowercase, \
include leading "#" (e.g. "#darkpsychology"). YouTube shows the first \
3 above the title.
  keywords : array of 10–20 short keyword phrases used for the video \
keywords meta field. Lowercase, no "#"."""


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
        ranked = [k for k, _ in sorted(freq.items(), key=lambda x: -x[1])]
        tags = ranked[:20]
        keywords = ranked[:15]
        opener = script.strip().split("\n\n")[0][:600]
        body = (
            f"{base} — what most people miss, explained simply.\n\n"
            f"{opener}\n\n"
            "What you'll learn:\n"
            "• The subtle patterns hiding in everyday behavior\n"
            "• Why most people overlook the warning signs\n"
            "• How to protect yourself without becoming paranoid\n\n"
            "If this resonated, hit like, subscribe for weekly drops, "
            "and tell us in the comments which sign hit closest to home."
        )
        hashtags = ["#psychology", "#humanbehavior", "#darkpsychology"]
        return {
            "title_variants": variants,
            "description_body": body,
            "tags": tags,
            "hashtags": hashtags,
            "keywords": keywords,
        }

    def _text_live(self, title: str, script: str) -> dict:
        from ..llm import anthropic_message  # lazy

        raw = anthropic_message(
            self.spec,
            system=_SEO_SYSTEM,
            user=f"Title: {title}\nScript:\n{script[:4000]}",
        )
        data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        # Defensive: an LLM occasionally drops one of the new fields. Backfill
        # from the offline path rather than failing the whole metadata stage.
        offline = self._text_offline(title, script)
        for k in ("title_variants", "description_body", "tags",
                  "hashtags", "keywords"):
            if not data.get(k):
                data[k] = offline[k]
        return data

    def _compose_description(self, body: str, doc: ControlDocument,
                             music_credit: str | None,
                             hashtags: list[str]) -> str:
        """Assemble the publish-ready description.

        Order matters for YouTube: the first 2 lines appear above the
        fold, chapters must be on their own lines starting with a
        timestamp to trigger chapter markers, and hashtags at the very
        end (max 3 shown above the title — YouTube ignores beyond 15).
        """
        parts: list[str] = [body.strip()]

        chapters = _chapters_timestamps(doc)
        if chapters:
            parts.append("📑 Chapters\n" + "\n".join(chapters))

        if music_credit:
            parts.append(f"🎵 Music\n{music_credit}")

        parts.append(f"⚠️ {DISCLOSURE}")

        if hashtags:
            parts.append(" ".join(hashtags[:15]))

        return "\n\n".join(parts)

    def run(self, video_path: Path, doc: ControlDocument, script: str,
            out_dir: Path, *,
            music_design: dict | None = None,
            language: str | None = None) -> dict:
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

        music_credit = _load_music_attribution(music_design)
        description = self._compose_description(
            text["description_body"], doc, music_credit, text["hashtags"])

        # YouTube uses BCP-47 primary subtag (e.g. "en", not "en-US") for
        # defaultLanguage. Strip the region if present; default to English.
        lang = (language or "en").split("-")[0].lower()

        meta = {
            "title": text["title_variants"][0],
            "title_variants": text["title_variants"],
            "description": description,           # publish-ready, full body
            "description_body": text["description_body"],  # raw SEO body only
            "description_seo": description,       # alias for upload path
            "tags": text["tags"],
            "hashtags": text["hashtags"],
            "keywords": text["keywords"],
            "chapters": _chapters_timestamps(doc),
            "thumbnail": str(thumb),
            "music_credit": music_credit,
            "synthetic_media_disclosure": True,   # G11 / YouTube altered-content
            "category": "Education",
            "category_id": "27",                  # YouTube: Education
            "default_language": lang,
            "default_audio_language": lang,
            "made_for_kids": False,
            "privacy_status": "private",          # G15: forced until OAuth verified
        }
        (out_dir / "metadata.json").write_text(json.dumps(meta, indent=2),
                                               encoding="utf-8")
        return meta
