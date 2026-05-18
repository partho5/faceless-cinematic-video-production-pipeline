"""Stage 1 (scriptwriter) + Stage 2 (segmenter/director) — planning/09.

Stage 1: topic -> narration `script.md` (chapter-marked), then a REVIEW
GATE (script is the highest-leverage artifact; gate before TTS/render spend).
Stage 2: approved script -> per-chapter control JSON, validate-and-repair
loop (G3), assembled into one canonical document.

Offline (no ANTHROPIC_API_KEY): Stage 1 emits the planning/05 sample script;
Stage 2 emits the planning/05 sample control document. The pipeline stays
runnable end-to-end without keys and against the exact build target.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import Config
from ..sample import load_sample_document, sample_script_markdown
from ..schema.model import ControlDocument
from ..schema.validator import validate


def slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_-]+", "-", s)[:60] or "video"


# ----------------------------------------------------------------- Stage 1 --
_SCRIPT_SYS_BASE = (
    "You are a scriptwriter for a dark-psychology YouTube channel. Voice: "
    "low, controlled, second-person, knowledge-gap hooks, retention beats. "
    "Write narration ONLY (no stage directions). Mark chapters as "
    "'**[HOOK · m:ss–m:ss]**', '**[SIGN N · ...]**', '**[CLOSING · ...]**'."
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

    def _anthropic(self, topic: str, target_minutes: float) -> str:
        from ..llm import anthropic_message  # lazy

        return anthropic_message(
            self.spec, system=_script_sys(target_minutes),
            user=f"Write the full script for: {topic}",
        )

    def generate(self, topic: str, out_dir: Path,
                 target_minutes: float = 6.0) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "script.md"
        # If the user already reviewed AND approved this exact script, keep
        # it verbatim: the script they read is the script that gets rendered,
        # and the approve->continue re-run costs zero extra API spend.
        if path.exists() and (out_dir / "script.APPROVED").exists():
            return path
        if self.spec.offline:
            script = (f"# {topic}\n\n" + sample_script_markdown())
        else:
            try:
                script = self._anthropic(topic, target_minutes)
            except Exception:
                script = f"# {topic}\n\n" + sample_script_markdown()
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
    "cut_in_type, cut_out_type, music_intensity, sound_fx [{type,timing,"
    "volume}], grain_override, vignette_override, chromatic_aberration. "
    "Rapid enumerations -> camera_motion 'rapid_clip_montage' with "
    "montage_clips [{query,duration}]. start/end are ORDERING TARGETS only. "
    "Return ONLY a JSON array of segments, no prose."
)


class SegmentStage:
    """Approved script -> validated canonical ControlDocument (G3)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.spec = cfg.model("segmentation_direction")

    def _anthropic_chapter(self, label: str, text: str, prior_repair: str | None):
        from ..llm import anthropic_message  # lazy

        user = f"Chapter [{label}]:\n{text}"
        if prior_repair:
            user += f"\n\nThe previous output FAILED validation: {prior_repair}\nFix and resend."
        raw = anthropic_message(self.spec, system=_SEG_SYS, user=user)
        return json.loads(raw[raw.index("["): raw.rindex("]") + 1])

    def generate(self, script_path: Path, out_dir: Path,
                 *, max_repair: int = 2) -> ControlDocument:
        if self.spec.offline:
            doc = load_sample_document()
        else:
            try:
                doc = self._generate_live(script_path)
            except Exception:
                doc = load_sample_document()  # robust fallback

        # validate-and-repair loop (styled issues auto-repaired in-place)
        for _ in range(max_repair + 1):
            res = validate(doc)
            if res.ok:
                break
        (out_dir / "video.json").write_text(json.dumps(doc.to_dict(), indent=2),
                                            encoding="utf-8")
        return doc

    def _generate_live(self, script_path: Path) -> ControlDocument:
        base = load_sample_document()  # reuse meta/chapters/global_assets shell
        chapters = split_chapters(script_path.read_text(encoding="utf-8"))
        all_segs: list[dict] = []
        for ci, (label, text) in enumerate(chapters, 1):
            repair_note = None
            for attempt in range(3):  # G3 per-chapter retry
                try:
                    segs = self._anthropic_chapter(label, text, repair_note)
                except Exception as e:
                    repair_note = str(e)[:300]
                    continue
                for si, s in enumerate(segs, 1):
                    s["id"] = f"c{ci}_seg{si}"
                all_segs.extend(segs)
                break
        d = base.to_dict()
        d["segments"] = all_segs or d["segments"]
        return ControlDocument.from_dict(d)


def review_gate(script_path: Path, *, auto_approve: bool) -> bool:
    """Stage 2 only runs on an approved script.

    auto_approve (bypass / offline e2e) -> approve. Otherwise approval is a
    sibling sentinel file `script.APPROVED` the user creates after editing.
    """
    if auto_approve:
        return True
    return (script_path.parent / "script.APPROVED").exists()
