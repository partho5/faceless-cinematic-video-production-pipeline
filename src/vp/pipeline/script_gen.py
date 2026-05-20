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
from ..sample import load_sample_document
from ..schema.model import ControlDocument
from ..schema.validator import validate


def _log(msg: str) -> None:
    # same '[vp] ' prefix as vp.run so it streams into the GUI log pane
    print(f"[vp] {msg}", flush=True)


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
        base = load_sample_document()  # reuse meta/chapters/global_assets shell
        chapters = split_chapters(script_path.read_text(encoding="utf-8"))
        n = len(chapters)
        _log(f"stage2: segmenting {n} chapter(s) via LLM "
             f"(one API call each)…")
        ch_cache = out_dir / "_seg_chapters"
        ch_cache.mkdir(parents=True, exist_ok=True)
        all_segs: list[dict] = []
        for ci, (label, text) in enumerate(chapters, 1):
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
        d = base.to_dict()
        d["segments"] = all_segs
        if topic:
            d["video_meta"]["title"] = topic
        return ControlDocument.from_dict(d)


def review_gate(script_path: Path, *, auto_approve: bool) -> bool:
    """Stage 2 only runs on an approved script.

    auto_approve (bypass / offline e2e) -> approve. Otherwise approval is a
    sibling sentinel file `script.APPROVED` the user creates after editing.
    """
    if auto_approve:
        return True
    return (script_path.parent / "script.APPROVED").exists()
