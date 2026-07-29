"""Typed model for the control document (planning/04).

The canonical in-pipeline document is ONE dict:

    {video_meta, chapters[], global_assets, segments[]}

Stage 2 emits segments per chapter; they are concatenated into `segments`
with globally-unique ids, then validated as one document here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SoundFX:
    """A resolved sound cue placed by the SoundDesigner pass.

    `type` is now a CATALOG id (one of the curated 20), not a free-form name.
    `timing` is the resolved offset **in seconds from the segment's audio
    start** (build_master adds the segment's absolute timeline start).
    `intensity` ∈ {soft,normal,hard} drives the can't-bury-voice gain model
    in master.py; `volume` is kept only for back-compat / legacy docs and is
    no longer the gain lever. `reason` is the editor's justification (logged,
    not acted on — forces the model to earn each cue).
    """
    type: str
    timing: float
    volume: float = 0.7
    intensity: str = "normal"
    reason: str = ""


@dataclass
class Emphasis:
    word: str
    effect: str
    color_shift: str | None = None


@dataclass
class MontageClip:
    query: str
    duration: float


@dataclass
class Segment:
    id: str
    beat_type: str
    start: float
    end: float
    # spoken_text: the ACTUAL words sent to TTS — mechanically sliced from the
    # approved script (never LLM-paraphrased), so narration content can never
    # be silently dropped the way a free-form caption rewrite could.
    # text_overlay: the on-screen caption. MUST mirror spoken_text exactly
    # (script_gen.py enforces this) — fx/text.py word-times captions against
    # the forced alignment of the spoken audio, so a caption with different
    # or fewer words than what's actually spoken breaks sync and silently
    # kills word-emphasis highlighting (a real regression this comment is
    # here to prevent repeating).
    text_overlay: str = ""
    spoken_text: str = ""
    tts_scene: str = ""
    tts_delivery: str = ""
    audio_path: str | None = None
    pre_silence_ms: int = 0
    post_silence_ms: int = 0
    text_personality: str = "clinical"
    text_color: str = "#FFFFFF"
    text_position: str = "center"
    text_animation_in: str = "word_by_word_fade"
    text_animation_emphasis: list[Emphasis] = field(default_factory=list)
    text_animation_out: str = "fade_dissolve"
    camera_motion: str = "locked_frame"
    camera_scale_start: float = 1.0
    camera_scale_end: float = 1.0
    clip_query_primary: str = ""
    clip_query_backup: str = ""
    montage_clips: list[MontageClip] = field(default_factory=list)
    color_grade_override: str | None = None
    cut_in_type: str = "hard_cut"
    cut_out_type: str = "hard_cut"
    music_intensity: float = 0.4
    music_type: str | None = None
    sound_fx: list[SoundFX] = field(default_factory=list)
    grain_override: float | None = None
    vignette_override: float | None = None
    chromatic_aberration: float = 0.0
    # filled downstream (G1): real measured audio window
    real_start: float | None = None
    real_end: float | None = None

    @property
    def planned_duration(self) -> float:
        return round(self.end - self.start, 4)

    @property
    def duration(self) -> float:
        if self.real_start is not None and self.real_end is not None:
            return round(self.real_end - self.real_start, 4)
        return self.planned_duration


@dataclass
class Chapter:
    chapter_id: str
    start: float
    end: float
    intensity_curve: str = ""
    segment_count: int = 0


@dataclass
class ControlDocument:
    video_meta: dict[str, Any]
    chapters: list[Chapter]
    global_assets: dict[str, Any]
    segments: list[Segment]

    # ---- (de)serialization -------------------------------------------------
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ControlDocument":
        chapters = [Chapter(**c) for c in d.get("chapters", [])]
        segments = [_segment_from_dict(s) for s in d.get("segments", [])]
        return cls(
            video_meta=d.get("video_meta", {}),
            chapters=chapters,
            global_assets=d.get("global_assets", {}),
            segments=segments,
        )

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return {
            "video_meta": self.video_meta,
            "chapters": [asdict(c) for c in self.chapters],
            "global_assets": self.global_assets,
            "segments": [asdict(s) for s in self.segments],
        }


def _num(v: Any, default: float = 0.0) -> float:
    """Coerce LLM-emitted numbers: float, '1.5', 'm:ss', '' -> float.

    Stage-2 Claude sometimes emits start/end as strings or 'm:ss'. Planned
    times are ordering hints only (G1 reflow is authoritative), so lenient
    coercion here is correct, not lossy.
    """
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        t = v.strip()
        if not t:
            return default
        if ":" in t:  # m:ss or h:mm:ss
            try:
                parts = [float(p) for p in t.split(":")]
                out = 0.0
                for p in parts:
                    out = out * 60 + p
                return out
            except ValueError:
                return default
        try:
            return float(t)
        except ValueError:
            return default
    return default


_FLOAT_FIELDS = {
    "start": 0.0, "end": 0.0, "camera_scale_start": 1.0,
    "camera_scale_end": 1.0, "music_intensity": 0.4,
    "chromatic_aberration": 0.0,
}
_OPT_FLOAT_FIELDS = ("grain_override", "vignette_override")
_INT_FIELDS = ("pre_silence_ms", "post_silence_ms")


def _segment_from_dict(s: dict[str, Any]) -> Segment:
    s = dict(s)
    s["sound_fx"] = [
        SoundFX(type=fx.get("type", "thud"),
                timing=_num(fx.get("timing", 0.0)),
                volume=_num(fx.get("volume", 0.7), 0.7),
                intensity=str(fx.get("intensity", "normal")),
                reason=str(fx.get("reason", "")))
        for fx in s.get("sound_fx", [])
    ]
    s["text_animation_emphasis"] = [
        Emphasis(**e) for e in s.get("text_animation_emphasis", [])
    ]
    s["montage_clips"] = [
        MontageClip(query=m.get("query", ""), duration=_num(m.get("duration", 1.5), 1.5))
        for m in s.get("montage_clips", [])
    ]
    for f, d in _FLOAT_FIELDS.items():
        if f in s:
            s[f] = _num(s[f], d)
    for f in _OPT_FLOAT_FIELDS:
        if s.get(f) is not None:
            s[f] = _num(s[f], 0.0)
    for f in _INT_FIELDS:
        if f in s:
            s[f] = int(_num(s[f], 0.0))
    known = Segment.__dataclass_fields__.keys()
    return Segment(**{k: v for k, v in s.items() if k in known})
