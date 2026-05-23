"""Validate / repair a ControlDocument before render (planning/04).

Contract:
  - STRUCTURAL problems => errors (reject; never render).
  - STYLED problems (unknown enum, out-of-range fx) => repaired in place +
    a warning. The doc is mutated to a safe, renderable state.

`check_audio=True` additionally asserts every segment's audio_path exists —
only call that *after* the TTS stage (pre-TTS the paths are not written yet).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .enums import (
    CAMERA_MOTION, COLOR_GRADE, CUT_TYPE, SAFE_DEFAULTS, TEXT_ANIM_EMPHASIS,
    TEXT_ANIM_IN, TEXT_ANIM_OUT, TEXT_PERSONALITY, TEXT_POSITION,
)
from .model import ControlDocument, Segment


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def __str__(self) -> str:
        lines = [f"validation: {'OK' if self.ok else 'FAILED'}"]
        lines += [f"  ERROR  {e}" for e in self.errors]
        lines += [f"  warn   {w}" for w in self.warnings]
        return "\n".join(lines)


_FUZZY = 0.05  # seconds tolerance for timeline contiguity


def _repair_enum(seg: Segment, attrname: str, allowed: set[str], r: ValidationResult):
    val = getattr(seg, attrname)
    if val is None or val in allowed:
        return
    safe = SAFE_DEFAULTS.get(attrname, next(iter(allowed)))
    r.warnings.append(
        f"{seg.id}.{attrname}='{val}' unknown -> repaired to '{safe}'"
    )
    setattr(seg, attrname, safe)


def validate(
    doc: ControlDocument,
    *,
    assets_root: Path | None = None,
    check_audio: bool = False,
    audio_root: Path | None = None,
) -> ValidationResult:
    r = ValidationResult()
    meta = doc.video_meta

    # ---- video meta -------------------------------------------------------
    if not meta.get("title"):
        r.errors.append("video_meta.title missing")
    total = float(meta.get("total_duration_seconds", 0) or 0)
    if total <= 0:
        r.errors.append("video_meta.total_duration_seconds must be > 0")

    # ---- chapters cover the timeline -------------------------------------
    chapters = sorted(doc.chapters, key=lambda c: c.start)
    if not chapters:
        r.errors.append("no chapters")
    else:
        if abs(chapters[0].start) > _FUZZY:
            r.errors.append(f"first chapter starts at {chapters[0].start}, not 0")
        for a, b in zip(chapters, chapters[1:]):
            if abs(a.end - b.start) > _FUZZY:
                r.errors.append(
                    f"chapter gap/overlap: {a.chapter_id} ends {a.end}, "
                    f"{b.chapter_id} starts {b.start}"
                )
        if total and abs(chapters[-1].end - total) > _FUZZY:
            r.warnings.append(
                f"last chapter ends {chapters[-1].end} != total {total} "
                f"(planned times are targets; G1 reflow handles real timing)"
            )

    # ---- segments ---------------------------------------------------------
    if not doc.segments:
        r.errors.append("no segments")
    seen_ids: set[str] = set()
    segs = sorted(doc.segments, key=lambda s: s.start)
    for s in segs:
        if s.id in seen_ids:
            r.errors.append(f"duplicate segment id '{s.id}'")
        seen_ids.add(s.id)

        if s.end <= s.start:
            # start/end are ordering targets only; G1 reflow overwrites with
            # real audio-measured timing. Repair rather than reject.
            r.warnings.append(
                f"{s.id}: end {s.end} <= start {s.start} "
                f"-> synthetic +2 s slot (G1 reflow overwrites)"
            )
            s.end = s.start + 2.0

        # TTS/caption fields: the main VoiceStage path uses text_overlay
        # only; tts_scene/tts_delivery feed the per-segment fallback
        # (tts_gemini.py). All three are auto-repaired with mutual fallback
        # rather than rejected — an LLM that drops one field on a
        # caption-only beat must not block the entire render.
        ts = (s.tts_scene or "").strip()
        to = (s.text_overlay or "").strip()
        if not ts and to:
            s.tts_scene = to
            r.warnings.append(
                f"{s.id}: tts_scene empty -> repaired from text_overlay"
            )
        elif not to and ts:
            s.text_overlay = ts
            r.warnings.append(
                f"{s.id}: text_overlay empty -> repaired from tts_scene"
            )
        elif not ts and not to:
            # Both empty: the segment carries no spoken or visible content.
            # Stamp a minimal placeholder so the chapter-based TTS still
            # emits *something* (a 1-syllable beat) instead of skipping the
            # whole chapter when every segment is empty. Better silent
            # filler than a crashed reflow.
            placeholder = "..."
            s.text_overlay = placeholder
            s.tts_scene = placeholder
            r.warnings.append(
                f"{s.id}: text_overlay AND tts_scene empty "
                f"-> placeholder '...' (segment will read as a brief pause)"
            )
        if not (s.tts_delivery and s.tts_delivery.strip()):
            s.tts_delivery = "natural narration"
            r.warnings.append(
                f"{s.id}: tts_delivery empty -> 'natural narration'"
            )

        # silence pacing sane
        for fld in ("pre_silence_ms", "post_silence_ms"):
            v = getattr(s, fld)
            if v < 0 or v >= 3000:
                r.warnings.append(f"{s.id}.{fld}={v} out of [0,3000) -> clamped")
                setattr(s, fld, max(0, min(2999, int(v))))

        # styled enums -> repair
        _repair_enum(s, "text_personality", TEXT_PERSONALITY, r)
        _repair_enum(s, "text_position", TEXT_POSITION, r)
        _repair_enum(s, "text_animation_in", TEXT_ANIM_IN, r)
        _repair_enum(s, "text_animation_out", TEXT_ANIM_OUT, r)
        _repair_enum(s, "camera_motion", CAMERA_MOTION, r)
        _repair_enum(s, "cut_in_type", CUT_TYPE, r)
        _repair_enum(s, "cut_out_type", CUT_TYPE, r)
        if not isinstance(s.color_grade_override, str):
            s.color_grade_override = None
        if s.color_grade_override and s.color_grade_override not in COLOR_GRADE:
            r.warnings.append(
                f"{s.id}.color_grade_override='{s.color_grade_override}' unknown "
                f"-> base grade"
            )
            s.color_grade_override = None
        for e in s.text_animation_emphasis:
            if e.effect not in TEXT_ANIM_EMPHASIS:
                r.warnings.append(
                    f"{s.id}: emphasis effect '{e.effect}' unknown -> 'brief_glow'"
                )
                e.effect = "brief_glow"

        # sound fx ranges
        for fx in s.sound_fx:
            if not (0.0 <= fx.volume <= 1.0):
                r.warnings.append(f"{s.id}: sfx '{fx.type}' volume {fx.volume} -> clamped")
                fx.volume = max(0.0, min(1.0, fx.volume))
            if fx.timing < 0 or fx.timing > s.planned_duration + _FUZZY:
                r.warnings.append(
                    f"{s.id}: sfx '{fx.type}' timing {fx.timing} outside "
                    f"segment 0..{s.planned_duration} -> clamped"
                )
                fx.timing = max(0.0, min(s.planned_duration, fx.timing))

        # montage consistency
        if s.camera_motion == "rapid_clip_montage" and not s.montage_clips:
            r.warnings.append(
                f"{s.id}: rapid_clip_montage but no montage_clips -> single clip"
            )
        if not s.clip_query_primary and not s.montage_clips:
            r.warnings.append(f"{s.id}: no clip query -> generic mood fallback")

        # audio existence (post-TTS only)
        if check_audio:
            if not s.audio_path:
                r.errors.append(f"{s.id}: audio_path not set")
            else:
                ap = Path(s.audio_path)
                if audio_root and not ap.is_absolute():
                    ap = audio_root / ap
                if not ap.exists():
                    r.errors.append(f"{s.id}: audio file missing: {ap}")

    # segment timeline contiguity (planned; warn-only because G1 reflows)
    for a, b in zip(segs, segs[1:]):
        if abs(a.end - b.start) > _FUZZY:
            r.warnings.append(
                f"planned segment gap: {a.id} ends {a.end}, {b.id} starts {b.start}"
            )

    # referenced assets present in global_assets table (structural)
    fonts = doc.global_assets.get("fonts", {})
    needed_personalities = {s.text_personality for s in doc.segments}
    for p in needed_personalities:
        if p not in fonts:
            r.errors.append(f"global_assets.fonts has no entry for personality '{p}'")
    luts = doc.global_assets.get("luts", {})
    for s in doc.segments:
        g = s.color_grade_override
        if g and g not in luts:
            r.warnings.append(f"{s.id}: grade '{g}' not in global_assets.luts (synth LUT)")

    return r


# --------------------------------------------------------- SFX cue safety ----
# Safety #1 of 2 (plan §1.3): "must-be-one-of-20" anti-hallucination check.
# Pure (no I/O, no alignment) so it is trivially unit-testable; anchor->time
# resolution lives in pipeline/sound_design.py where the alignments exist.
SFX_INTENSITIES = ("soft", "normal", "hard")


def validate_sfx_cues(
    cues: list[dict], allowed_ids: set[str]
) -> tuple[list[dict], list[str]]:
    """Filter raw SoundDesigner cues to a safe set.

    Contract (plan §4.3):
      - Drop any cue whose `sfx_id` is NOT a catalog id. **No substitution,
        no nearest-category, no synthesis** — silence beats a wrong sound.
      - Clamp `intensity` to {soft,normal,hard}; default/unknown -> 'normal'.
      - NO count cap. Restraint is the prompt's job (§1.2), never a
        mechanical rule. Returns every in-vocabulary cue unchanged.

    Returns (kept_cues, warnings). Each warning is a one-line audit string;
    the caller logs them (the drop is silent to the *render*, never silent
    to the *log*).
    """
    kept: list[dict] = []
    warns: list[str] = []
    for i, c in enumerate(cues):
        if not isinstance(c, dict):
            warns.append(f"sfx cue #{i}: not an object -> dropped")
            continue
        sid = c.get("sfx_id")
        if sid not in allowed_ids:
            warns.append(
                f"sfx cue #{i}: sfx_id={sid!r} not in catalog -> dropped "
                f"(no substitution)"
            )
            continue
        if not c.get("segment_id"):
            warns.append(f"sfx cue #{i} ({sid}): no segment_id -> dropped")
            continue
        c = dict(c)
        inten = c.get("intensity")
        if inten not in SFX_INTENSITIES:
            if inten is not None:
                warns.append(
                    f"sfx cue #{i} ({sid}): intensity={inten!r} unknown "
                    f"-> 'normal'"
                )
            c["intensity"] = "normal"
        kept.append(c)
    return kept, warns
