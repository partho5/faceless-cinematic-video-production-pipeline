"""Metadata stage (G2) — the biggest revenue lever.

- SEO-optimized title variants / description / tags / hashtags / keywords /
  chapters via the `metadata_text` model (lazy Anthropic), with a
  deterministic offline fallback.
- `thumbnail_prompt`: a copy/paste-ready prompt for Gemini's free web UI
  ("nano banana" / gemini-2.5-flash-image). Brand voice (accent colors,
  mood, lighting, style) comes from `branding.thumbnail` in config.yaml
  and is enforced on every video regardless of topic so the channel
  reads as one channel.
- Composes a publish-ready description (hook → body → chapters → music
  credit → disclosure → hashtags) so metadata.json is copy-paste-ready
  for YouTube Studio whether or not auto-upload is enabled.
- Always includes the synthetic-media disclosure line (G11) and the
  CC BY 4.0 music attribution when a track is used.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import ASSETS, Config
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


def _chapters_timestamps(doc: ControlDocument) -> list[str]:
    out = []
    for c in sorted(doc.chapters, key=lambda c: c.start):
        m, s = divmod(int(c.start), 60)
        name = c.chapter_id.replace("_", " ").title()
        out.append(f"{m:01d}:{s:02d} {name}")
    return out


def _branding_block(b: dict) -> str:
    """Render the user-tunable brand voice as a numbered rules block.

    Pasted into the LLM system prompt so every generated thumbnail prompt
    inherits the channel look. Also rendered into the deterministic
    offline fallback for parity.
    """
    accents = b.get("accent_colors") or []
    primary = accents[0] if accents else "#E84545"
    background = accents[1] if len(accents) > 1 else "#0F0F1A"
    return (
        f"  - Theme: {b['theme']}\n"
        f"  - Mood: {b['mood']}\n"
        f"  - Lighting: {b['lighting']}\n"
        f"  - Style: {b['style']}\n"
        f"  - Color palette: dominant background tone {background}; "
        f"single accent {primary} for the focal pop; no other vivid colors\n"
        f"  - Hard exclusions: {b['negative']}"
    )


_THUMB_RULES = """Thumbnail prompt rules (these are NON-NEGOTIABLE and apply \
to every video regardless of topic):
  1. ONE focal subject. A single iconic visual metaphor for the video's \
core idea. No collage, no multiple objects, no crowd, no split-screen.
  2. Lots of negative space. Rule-of-thirds. Depth. The eye lands on the \
subject in under one second at thumbnail size.
  3. NO text, lettering, captions, logos, watermarks, numbers, or symbols \
anywhere in the image. Title overlay is added separately later.
  4. Eye-catching via composition and light, not via clutter or saturation. \
High contrast against the dominant background tone.
  5. Premium / cinematic feel. Never a default-AI sheen; specify lens, \
grain, lighting direction.
  6. The prompt must be ONE paragraph, 60–120 words, written as a direct \
image-generation instruction (no preamble like "create a thumbnail that..."). \
End with the negative-prompt clause spelled out.

Brand voice to enforce on EVERY prompt:
{branding_block}"""


def _build_seo_system(branding: dict) -> str:
    """System prompt for `metadata_text`. Branding is interpolated so the
    LLM produces a `thumbnail_prompt` that already obeys the channel look.
    """
    return f"""You write SEO-optimized YouTube metadata. Return ONLY valid \
JSON, no prose. Every field must reflect THIS video's actual topic — do not \
default to any single niche, tone, or vocabulary.

Required keys:
  title_variants : array of 3 titles. Each ≤ 100 chars, hook-driven, \
curiosity-gap, faithful to the script's subject. Avoid clickbait \
punctuation like "!!!" or ALL CAPS words.
  description_body : 1200–1800 chars. Structure: (1) a 1–2 line hook \
that reuses the strongest title keywords; (2) a 4–6 line value-prop \
paragraph naturally weaving in 4–6 long-tail keywords from the script; \
(3) a short "What you'll learn" list of 3–5 bullets prefixed with "• "; \
(4) a 1-line CTA inviting like + subscribe + comment. Do NOT include \
chapter timestamps, music credits, hashtags, or the disclosure line — \
those are appended by the caller. Plain text only, no markdown.
  tags : array of 20–30 strings drawn from the script's actual subject \
matter. Mix broad (single-word, topic-defining) and long-tail (multi-word, \
search-intent phrases a viewer would type). Each tag ≤ 60 chars. No "#" \
prefix. Do not invent tags that don't match the video.
  hashtags : array of 3–5 short hashtags WITHOUT spaces, lowercase, \
include leading "#". Pick hashtags that match THIS video's topic — not \
generic channel hashtags. YouTube shows the first 3 above the title.
  keywords : array of 10–20 short keyword phrases for the video keywords \
meta field. Lowercase, no "#". Topic-relevant only.
  thumbnail_prompt : a SINGLE paragraph (60–120 words) to paste into \
Gemini's free web UI (nano banana / gemini-2.5-flash-image) to generate \
the YouTube thumbnail. Customize the focal subject to this video, then \
enforce the rules below verbatim.

{_THUMB_RULES.format(branding_block=_branding_block(branding))}"""


def _offline_thumbnail_prompt(title: str, branding: dict) -> str:
    """Deterministic fallback used when the LLM call is offline/fails.

    Topic-agnostic: only the title threads through; everything else is
    the channel brand voice. Worse than the live path, far better than
    the dead Pillow stamp it replaces.
    """
    accents = branding.get("accent_colors") or []
    primary = accents[0] if accents else "#E84545"
    background = accents[1] if len(accents) > 1 else "#0F0F1A"
    subject = title.strip().rstrip(".") or "the hidden human pattern"
    return (
        f"A 16:9 cinematic YouTube thumbnail visualising one iconic "
        f"metaphor for: \"{subject}\". A single focal subject anchored on "
        f"a rule-of-thirds intersection, surrounded by deep negative space. "
        f"{branding['style']}. {branding['lighting']}. Mood: "
        f"{branding['mood']}. Visual genre: {branding['theme']}. "
        f"Dominant background tone {background}; one restrained accent of "
        f"{primary} catching the rim of the subject — no other saturated "
        f"colors. Composition reads instantly at small sizes; premium, "
        f"film-still quality, never default-AI gloss. Negative prompt: "
        f"{branding['negative']}."
    )


class MetadataStage:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.spec = cfg.model("metadata_text")
        self.branding = cfg.branding_thumbnail

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
            "thumbnail_prompt": _offline_thumbnail_prompt(title, self.branding),
        }

    def _text_live(self, title: str, script: str) -> dict:
        from ..llm import anthropic_message  # lazy

        raw = anthropic_message(
            self.spec,
            system=_build_seo_system(self.branding),
            user=f"Title: {title}\nScript:\n{script[:4000]}",
        )
        data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        # Defensive: an LLM occasionally drops one of the new fields. Backfill
        # from the offline path rather than failing the whole metadata stage.
        offline = self._text_offline(title, script)
        for k in ("title_variants", "description_body", "tags",
                  "hashtags", "keywords", "thumbnail_prompt"):
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
            "thumbnail_prompt": text["thumbnail_prompt"],
            "thumbnail_branding": self.branding,
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
