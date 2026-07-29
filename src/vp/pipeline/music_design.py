"""MusicDesigner — one LLM pass that picks a single background music track.

Macro-editorial decision: "what is the overall sonic feel of this video?"
Runs after Stage 2 validation, before TTS — needs only the validated doc
(title + niche + narration), not word timings or SFX cues.

Design mirrors sound_design.py:
  - closed vocabulary (catalog slugs only — unknown name = silence)
  - one dedicated API call, low temperature, tiny output
  - offline / any failure -> track=None (no music, never a crash)
  - result is checkpointed to out/music.json by run.py for resume support

The LLM decides:
  track    — which slug from the catalog
  presence — subtle | moderate | prominent

Code decides everything else:
  music_master_volume  translated from presence tier
  music_fade_in_s      fixed 2.0s (after hook silence — see master.py)
  music_fade_out_s     fixed 2.5s
  hook silence         first-segment-aware, handled entirely in master.py
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import ASSETS, Config
from ..schema.model import ControlDocument

_CATALOG_PATH = ASSETS / "music" / "catalog.json"

# presence tier -> music_master_volume (fraction of full scale, pre-duck)
_PRESENCE_VOLUME: dict[str, float] = {
    "subtle":    0.10,
    "moderate":  0.18,
    "prominent": 0.26,
}

_FADE_IN_S  = 2.0   # after hook silence ends
_FADE_OUT_S = 2.5


def _log(msg: str) -> None:
    print(f"[vp] {msg}", flush=True)


# ----------------------------------------------------------------- catalog ---

def load_catalog() -> list[dict]:
    try:
        data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
        return data.get("tracks", []) if isinstance(data, dict) else []
    except Exception:
        return []


def catalog_slugs() -> set[str]:
    return {t.get("file", "").removesuffix(".mp3")
            for t in load_catalog() if t.get("file")}


def _slug_on_disk(slug: str) -> bool:
    return (ASSETS / "music" / f"{slug}.mp3").exists()


# ----------------------------------------------------------------- prompt ----

def _palette_block(tracks: list[dict]) -> str:
    lines = []
    for t in tracks:
        slug = t.get("file", "").removesuffix(".mp3")
        if not slug:
            continue
        buckets = ", ".join(t.get("mood_buckets", []))
        niches  = ", ".join(t.get("niches", []))
        energy  = t.get("energy", "?")
        lines.append(f"  {slug} | {buckets} | niches: {niches} | energy: {energy}")
    return "\n".join(lines)


def _system_prompt(tracks: list[dict]) -> str:
    return f"""\
You are a music supervisor for short-form online video. Given a video's title \
and opening narration, pick exactly ONE background music track from the \
catalog below that best fits the overall niche and emotional tone.

THE CATALOG (slug | mood buckets | target niches | energy):
{_palette_block(tracks)}

PRESENCE TIERS:
  subtle    — barely audible bed; fits narration-heavy, ASMR, tutorial content
  moderate  — clearly present but never competes with the voice (default)
  prominent — noticeable layer; fits hype, travel, cinematic, motivational

RULES:
- Pick the slug EXACTLY as listed. Any other value is invalid.
- Presence must be exactly: subtle, moderate, or prominent.
- If no track fits well, pick the closest one — silence is handled by \
setting track to null, which you should do ONLY if the video is ASMR or \
pure reaction content where any music would be intrusive.
- Do not explain your reasoning in the JSON; put it only in "reason".

OUTPUT — strict JSON, nothing else:
{{"track": "<slug or null>", "presence": "<subtle|moderate|prominent>", \
"reason": "<one line: niche + why this track>"}}"""


def _user_prompt(doc: ControlDocument) -> str:
    title = str(doc.video_meta.get("title", "") or "")
    # first ~150 words of narration — enough for tone/niche, cheap on tokens
    words: list[str] = []
    for s in doc.segments:
        words.extend((s.spoken_text or "").split())
        if len(words) >= 150:
            break
    opening = " ".join(words[:150])
    return f"VIDEO TITLE: {title}\n\nOPENING NARRATION:\n{opening}"


# ----------------------------------------------------------------- the pass --

class MusicDesigner:
    """Single LLM pass: picks track + presence. Writes nothing — caller checkpoints."""

    def __init__(self, cfg: Config) -> None:
        self.cfg  = cfg
        self.spec = cfg.model("music_design")

    def _call_llm(self, doc: ControlDocument, tracks: list[dict]) -> dict:
        if self.spec.offline:
            _log("music design: offline -> no music")
            return {}
        try:
            from ..llm import anthropic_message
            raw  = anthropic_message(
                self.spec,
                system=_system_prompt(tracks),
                user=_user_prompt(doc),
            )
            blob = raw[raw.find("{") : raw.rfind("}") + 1]
            return json.loads(blob) if blob else {}
        except Exception as e:
            _log(f"music design: LLM/parse failed ({str(e)[:120]}) -> no music")
            return {}

    def design(self, doc: ControlDocument) -> dict:
        """Pick a track. Returns a result dict safe to JSON-serialize and checkpoint.

        Always returns a complete dict — caller does doc.video_meta.update(result["meta_patch"]).
        """
        tracks  = load_catalog()
        allowed = catalog_slugs()

        raw    = self._call_llm(doc, tracks)
        slug   = raw.get("track") or None
        pres   = raw.get("presence", "moderate")
        reason = str(raw.get("reason", ""))

        # validate slug: must be in catalog AND file must exist on disk
        if slug is not None:
            if slug not in allowed:
                _log(f"music design: unknown slug {slug!r} -> no music")
                slug = None
            elif not _slug_on_disk(slug):
                _log(f"music design: {slug}.mp3 not on disk -> no music")
                slug = None

        # validate presence tier
        if pres not in _PRESENCE_VOLUME:
            pres = "moderate"

        volume = _PRESENCE_VOLUME[pres] if slug else 0.0

        # pull loop window from catalog so build_master slices correctly
        loop_start_s: float | None = None
        loop_end_s:   float | None = None
        if slug:
            for t in tracks:
                if t.get("file", "").removesuffix(".mp3") == slug:
                    loop_start_s = t.get("loop_start_s")
                    loop_end_s   = t.get("loop_end_s")
                    break

        meta_patch: dict = {
            "background_music_track": slug,
            "music_master_volume":    volume,
            "music_fade_in_s":        _FADE_IN_S,
            "music_fade_out_s":       _FADE_OUT_S,
            "music_loop_start_s":     loop_start_s,
            "music_loop_end_s":       loop_end_s,
        }

        if slug:
            _log(f"music design: {slug} [{pres}] vol={volume} — {reason}")
        else:
            _log("music design: no track selected -> silence")

        return {
            "meta_patch": meta_patch,
            "track":      slug,
            "presence":   pres,
            "reason":     reason,
            "offline":    bool(self.spec.offline),
            "model":      self.spec.model,
        }
