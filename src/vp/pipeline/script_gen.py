"""Stage 1 (scriptwriter) + Stage 2 (segmenter/director) — planning/09.

Stage 1: topic -> narration `script.md` (chapter-marked), then a REVIEW
GATE (script is the highest-leverage artifact; gate before TTS/render spend).
Stage 2: approved script -> per-chapter control JSON, validate-and-repair
loop (G3), assembled into one canonical document.

Missing or failing API keys raise immediately — no silent fallback to sample data.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import Config
from ..schema.model import ControlDocument
from ..schema.validator import validate


# Channel-wide defaults baked in at Stage 2. These USED to be inherited
# from planning/05-sample-case.md via load_sample_document(), which leaked
# the sample's relationship-manipulation chapters and topic-specific
# metadata into every shipped video. Defining them here decouples the
# production pipeline from the sample fixture (which now exists only for
# tests).
#
# Only neutral look-numbers and mix defaults live here. The mood-coded
# `base_color_grade` is read from `config.yaml -> channel.base_color_grade`
# so the channel's visual identity is a config knob, not a code constant.
# `master.py` reads the volume fields; `MusicDesigner` overwrites
# `background_music_track` + `music_master_volume` per-video when
# --add-music is enabled.
_CHANNEL_VIDEO_META: dict = {
    "base_grain": 0.22,
    "base_vignette": 0.35,
    "base_chromatic_aberration": 0.05,
    "music_master_volume": 0.18,
    "voice_master_volume": 1.0,
    "music_duck_amount": 0.7,
}

# global_assets pointer table: maps style names -> filenames under assets/.
# Channel-wide; not per-video. Fonts live in assets/fonts/, LUTs in assets/luts/.
_CHANNEL_GLOBAL_ASSETS: dict = {
    "fonts": {
        "aggressive": "Anton-Regular.ttf",
        "clinical": "Inter-Bold.ttf",
        "whisper": "Cormorant-Italic.ttf",
        "reveal": "PlayfairDisplay-Bold.ttf",
        "handwritten": "Caveat-Bold.ttf",
    },
    # Pointer table for every grade in schema.enums.COLOR_GRADE. If the
    # .cube file isn't present in assets/luts/ the FX layer synthesises one;
    # the table just keeps the validator from warning per-segment.
    "luts": {
        "cold_isolation":    "luts/cold_isolation.cube",
        "warm_comfort":      "luts/warm_comfort.cube",
        "warm_comfort_dark": "luts/warm_comfort_dark.cube",
        "clinical":          "luts/clinical.cube",
        "surveillance":      "luts/surveillance.cube",
        "memory":            "luts/memory.cube",
        "threat":            "luts/threat.cube",
        "madness":           "luts/madness.cube",
        "revelation":        "luts/revelation.cube",
        "death":             "luts/death.cube",
        "dream":             "luts/dream.cube",
        "interrogation":     "luts/interrogation.cube",
        "nostalgia":         "luts/nostalgia.cube",
    },
}


def _log(msg: str) -> None:
    # same '[vp] ' prefix as vp.run so it streams into the GUI log pane
    print(f"[vp] {msg}", flush=True)


def slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_-]+", "-", s)[:60] or "video"


# ----------------------------------------------------------------- Stage 1 --
_SCRIPT_SYS_BASE = (
    "You are a scriptwriter for a YouTube channel. Write narration ONLY "
    "(no stage directions, no camera notes). Use proven retention craft: "
    "a strong cold-open hook, knowledge-gap turns, beats that earn the next "
    "sentence, and an ending that lands. "
    "Match voice, register, and intensity to the TOPIC — calm and warm for "
    "a bedtime story, precise and grounded for a how-to, lively for a "
    "review, controlled and dramatic for a thriller breakdown. Do not "
    "default to any single tone. "
    "Mark each chapter on its own line as '**[<SHORT_LABEL> · m:ss–m:ss]**' "
    "where the label is a topic-appropriate name (HOOK / INTRO / STEP 1 / "
    "PART 2 / SECTION / MAIN POINT / SIGN N / CLOSING — pick whatever fits "
    "the topic). The first chapter must be HOOK and the last must be "
    "CLOSING; everything between is yours to name."
)

_WPM = 150  # calm spoken pace; words ≈ minutes × WPM


def _script_sys(target_minutes: float) -> str:
    m = max(0.5, float(target_minutes))
    words = int(round(m * _WPM))
    return (
        _SCRIPT_SYS_BASE
        + f" Target ~{_WPM} spoken words per minute. The full narration "
        f"should run about {m:g} minute(s) read aloud — roughly {words} "
        f"words. Get close, it need not be exact. Scale the number of "
        f"chapters/beats to fill that length naturally (don't pad or rush)."
    )


class ScriptStage:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.spec = cfg.model("script_generation")

    def _anthropic(self, topic: str, target_minutes: float,
                   hint: str | None = None) -> str:
        from ..llm import anthropic_message  # lazy

        user = f"Write the full script for: {topic}"
        if hint and hint.strip():
            user += (
                "\n\nUse the following writer's hints / raw story as the "
                "basis — honor its facts, beats and intent; rewrite it into "
                "the channel's narration voice and chapter structure:\n"
                f"\"\"\"\n{hint.strip()}\n\"\"\""
            )
        return anthropic_message(
            self.spec, system=_script_sys(target_minutes), user=user,
        )

    def generate(self, topic: str, out_dir: Path,
                 target_minutes: float = 6.0,
                 hint: str | None = None) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "script.md"
        # If the user already reviewed AND approved this exact script, keep
        # it verbatim: the script they read is the script that gets rendered,
        # and the approve->continue re-run costs zero extra API spend.
        if path.exists() and (out_dir / "script.APPROVED").exists():
            _log("stage1: reusing approved script (0 API spend)")
            return path
        words = int(round(max(0.5, float(target_minutes)) * _WPM))
        _log(f"stage1: writing ~{words}-word script via "
             f"{self.spec.model}"
             f"{' (with hints)' if hint and hint.strip() else ''} "
             f"(one API call, ~10-30s)…")
        script = self._anthropic(topic, target_minutes, hint)
        path.write_text(script, encoding="utf-8")
        return path


def split_chapters(script_md: str) -> list[tuple[str, str]]:
    """-> [(chapter_label, chapter_text)] from the **[...]** markers."""
    parts = re.split(r"\*\*\[([^\]]+)\][^\n*]*\*\*", script_md)
    out = []
    for i in range(1, len(parts), 2):
        label = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if body:
            out.append((label, body))
    return out


_CH_LABEL_RX = re.compile(
    r"^(?P<name>.+?)\s*[·•|-]\s*"
    r"(?P<start>\d+:\d{2}(?::\d{2})?)\s*[-–—]\s*"
    r"(?P<end>\d+:\d{2}(?::\d{2})?)\s*$"
)


def _hms_to_s(t: str) -> float:
    """'m:ss' or 'h:mm:ss' -> seconds."""
    out = 0.0
    for p in t.split(":"):
        out = out * 60 + float(p)
    return out


def _chapter_slug(name: str) -> str:
    """'SIGN 1 Love Bombing' -> 'sign_1_love_bombing'."""
    s = re.sub(r"[^\w\s]", "", name.lower()).strip()
    return re.sub(r"\s+", "_", s) or "chapter"


def _parse_chapter_specs(chapters_parsed: list[tuple[str, str]]) -> list[dict]:
    """Derive validated Chapter dicts from the script's `**[NAME · m:ss–m:ss]**` markers.

    Returns dicts shaped for ControlDocument.from_dict: chapter_id, start,
    end, intensity_curve, segment_count. Guarantees a contiguous timeline
    starting at 0 (validator requirement). If any label is missing or
    unparseable, falls back to an even distribution.
    """
    specs: list[dict] = []
    parse_failed = False
    for label, _ in chapters_parsed:
        m = _CH_LABEL_RX.match(label)
        if not m:
            parse_failed = True
            break
        name = m.group("name").strip()
        specs.append({
            "chapter_id": _chapter_slug(name),
            "start": _hms_to_s(m.group("start")),
            "end": _hms_to_s(m.group("end")),
            "intensity_curve": "",
            "segment_count": 0,
        })

    if parse_failed or not specs:
        # script didn't carry timestamps in the labels — distribute evenly
        # over a placeholder 60s-per-chapter timeline. G1 reflow overwrites
        # with real audio timing anyway; this just keeps the validator happy.
        slot = 60.0
        specs = [{
            "chapter_id": _chapter_slug(label),
            "start": i * slot,
            "end": (i + 1) * slot,
            "intensity_curve": "",
            "segment_count": 0,
        } for i, (label, _) in enumerate(chapters_parsed)]
        return specs

    # Normalize to a contiguous timeline starting at 0 (validator: chapters
    # must abut, first chapter must start at 0).
    offset = specs[0]["start"]
    if offset != 0:
        for s in specs:
            s["start"] -= offset
            s["end"] -= offset
    for i in range(1, len(specs)):
        if specs[i]["start"] != specs[i - 1]["end"]:
            specs[i]["start"] = specs[i - 1]["end"]
        if specs[i]["end"] <= specs[i]["start"]:
            specs[i]["end"] = specs[i]["start"] + 1.0
    return specs


# ----------------------------------------------------------------- Stage 2 --
_SEG_SYS = (
    "You are a video director. Split the chapter into 2-4s beats at clause "
    "boundaries (~6-10 words each). For EACH segment emit a JSON object with: "
    "id, beat_type, start, end, text_overlay, tts_scene, tts_delivery, "
    "text_personality (aggressive|clinical|whisper|reveal|handwritten), "
    "pre_silence_ms, post_silence_ms (engineered dramatic pauses, ms, "
    "0-2500; bigger after hooks/revelations and sentence ends), "
    "text_color, text_position, text_animation_in, text_animation_emphasis "
    "[{word,effect,color_shift?}], text_animation_out, camera_motion, "
    "clip_query_primary, clip_query_backup, color_grade_override, "
    "cut_in_type, cut_out_type, music_intensity, "
    "grain_override, vignette_override, chromatic_aberration. "
    "Do NOT emit sound_fx — sound effects are chosen later by a dedicated "
    "editorial pass; inventing them here is ignored. "
    "Rapid enumerations -> camera_motion 'rapid_clip_montage' with "
    "montage_clips [{query,duration}]. start/end are ORDERING TARGETS only. "
    "Return ONLY a JSON array of segments, no prose."
)


def _repair_zero_timestamps(all_segs: list[dict], n_chapters: int,
                            total_duration: float) -> None:
    """Evenly distribute timestamps for any chapter where the LLM emitted all zeros.

    Gemini occasionally emits start=0/end=0 for all segments in a chapter.
    These are ordering targets only (G1 reflow overwrites with real audio timing),
    but the validator requires end > start. Assigns synthetic slots so the
    pipeline is not blocked.
    """
    total = float(total_duration or 0)
    slot = (total / n_chapters) if (total > 0 and n_chapters > 0) else 60.0
    for ci in range(1, n_chapters + 1):
        prefix = f"c{ci}_"
        ch = [s for s in all_segs if (s.get("id") or "").startswith(prefix)]
        if not ch:
            continue
        if not all(float(s.get("start", 0)) == 0 and float(s.get("end", 0)) == 0
                   for s in ch):
            continue  # LLM gave real timestamps — don't touch
        ch_start = (ci - 1) * slot
        step = slot / len(ch)
        for i, s in enumerate(ch):
            s["start"] = round(ch_start + i * step, 2)
            s["end"] = round(ch_start + (i + 1) * step, 2)


class SegmentStage:
    """Approved script -> validated canonical ControlDocument (G3)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.spec = cfg.model("segmentation_direction")

    def _anthropic_chapter(self, label: str, text: str, prior_repair: str | None):
        from ..llm import anthropic_message  # lazy
        from ..schema.model import _num

        user = f"Chapter [{label}]:\n{text}"
        if prior_repair:
            user += f"\n\nThe previous output FAILED validation: {prior_repair}\nFix and resend."
        raw = anthropic_message(self.spec, system=_SEG_SYS, user=user)
        segs = json.loads(raw[raw.index("["): raw.rindex("]") + 1])
        # Normalize start/end early — free-tier fallback models often emit
        # "m:ss" strings where Claude emits floats. _repair_zero_timestamps
        # runs on these raw dicts before the dataclass coerces them.
        for s in segs:
            if "start" in s:
                s["start"] = _num(s["start"])
            if "end" in s:
                s["end"] = _num(s["end"])
        return segs

    def generate(self, script_path: Path, out_dir: Path,
                 *, max_repair: int = 2, topic: str | None = None) -> ControlDocument:
        doc = self._generate_live(script_path, out_dir, topic=topic)

        # validate-and-repair loop (styled issues auto-repaired in-place)
        for _ in range(max_repair + 1):
            res = validate(doc)
            if res.ok:
                break
        (out_dir / "video.json").write_text(json.dumps(doc.to_dict(), indent=2),
                                            encoding="utf-8")
        return doc

    def _generate_live(self, script_path: Path, out_dir: Path,
                       *, topic: str | None = None) -> ControlDocument:
        chapters_parsed = split_chapters(script_path.read_text(encoding="utf-8"))
        n = len(chapters_parsed)
        if n == 0:
            raise RuntimeError("stage2: script has no '**[...]**' chapter markers")
        chapter_specs = _parse_chapter_specs(chapters_parsed)
        total_duration = chapter_specs[-1]["end"]
        _log(f"stage2: segmenting {n} chapter(s) via LLM "
             f"(one API call each)…")
        ch_cache = out_dir / "_seg_chapters"
        ch_cache.mkdir(parents=True, exist_ok=True)
        all_segs: list[dict] = []
        for ci, (label, text) in enumerate(chapters_parsed, 1):
            cache_file = ch_cache / f"{ci}.json"
            if cache_file.exists():
                try:
                    segs = json.loads(cache_file.read_text(encoding="utf-8"))
                    _log(f"stage2: chapter {ci}/{n} [{label}] (cached, skipping API call)")
                    all_segs.extend(segs)
                    continue
                except Exception:
                    pass
            repair_note = None
            for attempt in range(3):  # G3 per-chapter retry
                try:
                    segs = self._anthropic_chapter(label, text, repair_note)
                except Exception as e:
                    repair_note = str(e)[:300]
                    _log(f"stage2: chapter {ci}/{n} [{label}] "
                         f"attempt {attempt + 1}/3 failed: {str(e)[:120]}")
                    continue
                for si, s in enumerate(segs, 1):
                    s["id"] = f"c{ci}_seg{si}"
                cache_file.write_text(json.dumps(segs), encoding="utf-8")
                all_segs.extend(segs)
                _log(f"stage2: chapter {ci}/{n} [{label}] -> "
                     f"{len(segs)} segment(s)")
                break
        if not all_segs:
            raise RuntimeError("stage2: no segments produced from any chapter")
        _repair_zero_timestamps(all_segs, n, total_duration)

        # back-fill segment_count from what the LLM actually produced per
        # chapter (so the doc reflects reality, not the script's plan).
        for ci, spec in enumerate(chapter_specs, 1):
            prefix = f"c{ci}_"
            spec["segment_count"] = sum(
                1 for s in all_segs if (s.get("id") or "").startswith(prefix))

        d = {
            "video_meta": {
                **_CHANNEL_VIDEO_META,
                "base_color_grade": self.cfg.channel_base_color_grade,
                "title": topic or "Untitled",
                "total_duration_seconds": total_duration,
            },
            "chapters": chapter_specs,
            "global_assets": dict(_CHANNEL_GLOBAL_ASSETS),
            "segments": all_segs,
        }
        return ControlDocument.from_dict(d)


def review_gate(script_path: Path, *, auto_approve: bool) -> bool:
    """Stage 2 only runs on an approved script.

    auto_approve (bypass / offline e2e) -> approve. Otherwise approval is a
    sibling sentinel file `script.APPROVED` the user creates after editing.
    """
    if auto_approve:
        return True
    return (script_path.parent / "script.APPROVED").exists()
