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
_SCRIPT_SYS_BASE = """You are a scriptwriter for a YouTube channel. Write \
NARRATION ONLY — no stage directions, no camera notes, no scene labels in \
the spoken text. Plain prose, no markdown other than the chapter markers \
specified below.

VOICE — match the TOPIC; never default to a single tone:
  • Calm + warm for cozy / bedtime / lifestyle / cooking
  • Precise + grounded for how-to / tutorial / explainer / science
  • Lively + curious for review / commentary / pop-culture
  • Controlled + dramatic for thriller / true-crime / psychology
  • Second-person where natural ("you"); never "we, the channel".

HOOK CRAFT — the first chapter is HOOK. It is the most important block in \
the entire video. Build it to this exact shape:
  1) A concrete, specific observation that implies a hidden mechanism. The \
     first word does work. No setup, no "today", no preamble.
  2) The implication, addressing the viewer ("you"). Stake them in. Imply \
     they are already inside the situation.
  3) The promise — what the rest of the video reveals — phrased as \
     inevitability, not advertising. Tease the gap. DO NOT spoil the answer.

The whole hook is 3 sentences, ~6–8 seconds spoken. SPECIFICITY BEATS \
DRAMA: a number, place, time, or action does more work than any adjective. \
ONE idea per hook — never two.

FORBIDDEN OPENINGS — never start the script (or HOOK chapter) with these \
patterns or paraphrases of them:
  ✗ "In this video..." / "Today I'm going to..." / "Today we'll explore..."
  ✗ "Have you ever wondered..." / "What if I told you..."
  ✗ "Are you ready..." / "Buckle up..." / "Strap in..."
  ✗ "Welcome back..." / "Hey guys..." / "Hello everyone..."
  ✗ "You won't believe..." / "INCREDIBLE..." / any ALL-CAPS hype word
  ✗ Restating the title verbatim.
  ✗ A yes/no question the viewer answers in their head and leaves.

NO CLICKBAIT TELLS anywhere in the script — no "!!!", no ALL-CAPS hype, no \
"ultimate", no "shocking", no "mind-blowing", no exaggerated promises you \
don't deliver. Restraint signals confidence; exaggeration signals desperation.

BODY — each chapter after HOOK has a setup beat that EARNS the next \
sentence (a small revelation, a sharper specific, a turn). Pay out the \
hook's promise across the body. Cut anything a viewer would skip. No padding.

CLOSING — the last chapter is CLOSING. Land it: give the viewer something \
to carry out of the video (an action, a frame-shift, or one sentence that \
re-frames their thinking). Never recap what was said.

CHAPTERS — mark each on its own line as '**[<SHORT_LABEL> · m:ss–m:ss]**' \
where the label is topic-appropriate (HOOK / INTRO / STEP 1 / PART 2 / \
SECTION / MAIN POINT / SIGN N / FIX / TWIST / CLOSING — pick what fits). \
First chapter MUST be HOOK; last MUST be CLOSING. Time ranges must be \
contiguous and the last must equal the target total length."""

_WPM = 150  # calm spoken pace; words ≈ minutes × WPM


def _fmt_duration(minutes: float) -> str:
    """Return a human-readable duration string for LLM prompts.

    Examples:
        0.5  -> "about 30 seconds"
        1.0  -> "about 1 minute"
        1.5  -> "about 1 minute 30 seconds"
        6.0  -> "about 6 minutes"
    """
    total_sec = round(minutes * 60)
    m, s = divmod(total_sec, 60)
    if m == 0:
        return f"about {s} second{'s' if s != 1 else ''}"
    m_part = f"{m} minute{'s' if m != 1 else ''}"
    if s == 0:
        return f"about {m_part}"
    s_part = f"{s} second{'s' if s != 1 else ''}"
    return f"about {m_part} {s_part}"


def _script_sys(target_minutes: float) -> str:
    m = max(0.5, float(target_minutes))
    words = int(round(m * _WPM))
    return (
        _SCRIPT_SYS_BASE
        + f" Target ~{_WPM} spoken words per minute. The full narration "
        f"should run {_fmt_duration(m)} read aloud — roughly {words} "
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


_SENT_END_RE = re.compile(r"[.!?][\"'’”)]*$")
_CLAUSE_END_RE = re.compile(r"[,;:—–][\"'’”)]*$")


def split_spoken_chunks(text: str, *, target_min: int = 6,
                        target_max: int = 12) -> list[str]:
    """Deterministically slice approved narration into speakable beats.

    Lossless by construction: every whitespace-delimited token of `text`
    lands in exactly one output chunk, in the original order, so joining the
    chunks with spaces reproduces the source word-for-word. This replaces an
    earlier design where an LLM rewrote each chapter into beats directly —
    that free-form rewrite routinely paraphrased away entire clauses (e.g.
    subordinate "because..." clauses) that didn't fit its target beat length,
    and those words were never sent to TTS at all. Cutting mechanically means
    the LLM downstream only ever attaches direction metadata to a beat; it
    can no longer make content vanish.

    Prefers to cut at sentence ends once a chunk has >= target_min words,
    else at a clause boundary (comma/semicolon/dash) once it has >=
    target_max words, else forces a cut past 2x target_max so a clause-free
    run-on can't balloon into one unspeakable beat.
    """
    tokens = text.split()
    if not tokens:
        return []
    chunks: list[list[str]] = []
    cur: list[str] = []
    last = len(tokens) - 1
    for i, tok in enumerate(tokens):
        cur.append(tok)
        if i == last:
            break
        n = len(cur)
        if n >= target_min and _SENT_END_RE.search(tok):
            chunks.append(cur)
            cur = []
        elif n >= target_max and _CLAUSE_END_RE.search(tok):
            chunks.append(cur)
            cur = []
        elif n >= target_max * 2:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    # fold a too-short trailing fragment into its predecessor (cosmetic
    # beat-length cleanup only — still lossless, order preserved)
    if len(chunks) >= 2 and len(chunks[-1]) < max(1, target_min // 2):
        chunks[-2].extend(chunks[-1])
        chunks.pop()
    return [" ".join(c) for c in chunks]


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
    r"^(?P<name>.+?)\s*[·•|\-–—]\s*"
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
    "You are a video director. The chapter's narration has already been "
    "split for you into a NUMBERED list of FIXED spoken beats — this split "
    "is final. Do not reword, merge, split, reorder, add, or drop any beat; "
    "your job is only to attach direction metadata to each one, in the same "
    "order, one JSON object per beat. The beat's own words ARE the on-screen "
    "caption verbatim (captions are word-timed against the actual spoken "
    "audio, so the caption can never paraphrase or shorten the beat — do "
    "NOT emit text_overlay, it is filled in for you automatically). "
    "For EACH beat emit a JSON object with: "
    "id, beat_type, start, end, tts_scene, tts_delivery, "
    "text_personality (aggressive|clinical|whisper|reveal|handwritten), "
    "pre_silence_ms, post_silence_ms (engineered dramatic pauses, ms, "
    "0-2500; bigger after hooks/revelations and sentence ends), "
    "text_color, text_position, text_animation_in, text_animation_emphasis "
    "[{word,effect,color_shift?}], text_animation_out, "
    "camera_motion, clip_query_primary, clip_query_backup, "
    "color_grade_override, cut_in_type, cut_out_type, music_intensity, "
    "grain_override, vignette_override, chromatic_aberration. "
    "text_animation_emphasis is NOT required on every beat — most beats "
    "should have NONE at all; restraint is correct, not a fallback. Only "
    "when one specific word or short phrase in a beat genuinely carries "
    "outsized weight (a hard number, a diagnosis, a reversal, a striking "
    "claim) give it exactly one emphasis object with a fitting effect. The "
    "word MUST be copied verbatim from that beat's own text — one you "
    "invent or paraphrase will never be found and the highlight silently "
    "won't fire. "
    "Do NOT emit sound_fx — sound effects are chosen later by a dedicated "
    "editorial pass; inventing them here is ignored. "
    "Rapid enumerations -> camera_motion 'rapid_clip_montage' with "
    "montage_clips [{query,duration}]. "
    "REQUIRED non-empty strings on EVERY beat: tts_scene (a short "
    "scene-setting note for the voice director, e.g. 'a calm establishing "
    "moment' — NOT the spoken words, just the mood/setting), tts_delivery "
    "(one short delivery direction, e.g. 'calm, measured'). "
    "start/end are ordering hints only; use ABSOLUTE timestamps (m:ss from "
    "video start) matching the chapter's time window — do NOT restart at 0:00 "
    "per chapter. "
    "Return ONLY a JSON array of EXACTLY as many objects as there are "
    "numbered beats, in the same order, no prose, no trailing commas."
)


# Regex that strips trailing commas before } or ] — common LLM mistake the
# stdlib json parser rejects but jq/JSON5 tolerate.
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _extract_objects(window: str) -> list[dict]:
    """Brace-balanced scan that pulls every parseable {...} from `window`.

    Used as a salvage path when the array as a whole won't parse (a single
    broken segment shouldn't lose the rest of the chapter). Strings are
    tracked so braces inside string literals don't fool the depth counter.
    Returns whatever it could parse; never raises.
    """
    out: list[dict] = []
    i = 0
    n = len(window)
    while i < n:
        if window[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        esc = False
        j = i
        while j < n:
            c = window[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(window[i:j + 1])
                            if isinstance(obj, dict):
                                out.append(obj)
                        except (ValueError, json.JSONDecodeError):
                            try:
                                obj = json.loads(_TRAILING_COMMA_RE.sub(
                                    r"\1", window[i:j + 1]))
                                if isinstance(obj, dict):
                                    out.append(obj)
                            except (ValueError, json.JSONDecodeError):
                                pass
                        break
            j += 1
        i = j + 1
    return out


def _parse_segments_json(raw: str) -> list[dict]:
    """Robust array parser for Stage-2 LLM output.

    Strategies in order:
      1. Standard: extract [...] window, json.loads.
      2. Strip trailing commas (common LLM artifact), retry.
      3. Brace-balanced per-object salvage — keeps the chapter alive even
         if one segment is malformed or the array's closing ] was dropped.

    Raises ValueError only if zero objects can be salvaged. The caller's
    3-retry budget then triggers a repair prompt.
    """
    # 1. Standard path
    try:
        lo = raw.index("[")
        hi = raw.rindex("]") + 1
        window = raw[lo:hi]
    except ValueError:
        # No array brackets at all — fall back to scanning the whole string
        # for any {...} objects (Anthropic sometimes returns NDJSON-ish).
        salvaged = _extract_objects(raw)
        if salvaged:
            return salvaged
        raise ValueError("no JSON array or objects found in LLM output")

    try:
        data = json.loads(window)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    except (ValueError, json.JSONDecodeError):
        pass

    # 2. Trailing commas
    try:
        data = json.loads(_TRAILING_COMMA_RE.sub(r"\1", window))
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    except (ValueError, json.JSONDecodeError):
        pass

    # 3. Per-object salvage
    salvaged = _extract_objects(window)
    if salvaged:
        return salvaged
    raise ValueError(
        "stage 2 output unparseable even after trailing-comma + per-object "
        "salvage"
    )


def _repair_chapter_timestamps(all_segs: list[dict],
                               chapter_specs: list[dict]) -> None:
    """Re-base segments so every chapter's timestamps land in its window.

    Two real patterns the LLM produces (G3 cache evidence):
      A) all-zeros — no ordering hint at all. Distribute evenly across the
         chapter so validator's end>start check passes and segments sort in
         chapter order.
      B) per-chapter RELATIVE — segments start at 0:00 inside chapter N when
         chapter N actually begins at, say, 1:10. Re-base by adding (ch_start
         - first_seg_start) so segments don't interleave when the validator
         sorts by start.

    Timestamps are ordering hints only (G1 reflow overwrites with real audio
    time), so this is purely a cosmetic/ordering fix — but without it, the
    validator emits dozens of "planned segment gap" warnings AND any future
    sort-by-start pass would shuffle segments across chapters.
    """
    for ci, spec in enumerate(chapter_specs, 1):
        prefix = f"c{ci}_"
        ch = [s for s in all_segs if (s.get("id") or "").startswith(prefix)]
        if not ch:
            continue
        ch_start = float(spec.get("start", 0) or 0)
        ch_end = float(spec.get("end", ch_start + 60.0) or (ch_start + 60.0))
        ch_dur = max(1.0, ch_end - ch_start)

        starts = [float(s.get("start", 0) or 0) for s in ch]
        ends = [float(s.get("end", 0) or 0) for s in ch]

        # case A: all-zero — distribute evenly
        if all(s == 0 and e == 0 for s, e in zip(starts, ends)):
            step = ch_dur / len(ch)
            for i, s in enumerate(ch):
                s["start"] = round(ch_start + i * step, 2)
                s["end"] = round(ch_start + (i + 1) * step, 2)
            continue

        # case B: per-chapter relative — first segment starts well before the
        # chapter's expected start (>5s tolerance). Shift, don't scale; we
        # only need ordering correctness, not absolute accuracy.
        first_start = starts[0]
        if ch_start > 5.0 and first_start < ch_start - 5.0:
            offset = ch_start - first_start
            for s in ch:
                s["start"] = round(float(s.get("start", 0) or 0) + offset, 2)
                s["end"] = round(float(s.get("end", 0) or 0) + offset, 2)


class SegmentStage:
    """Approved script -> validated canonical ControlDocument (G3)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.spec = cfg.model("segmentation_direction")

    def _anthropic_chapter(self, label: str, chunks: list[str],
                           prior_repair: str | None):
        from ..llm import anthropic_message  # lazy
        from ..schema.model import _num

        numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(chunks, 1))
        user = (
            f"Chapter [{label}] — {len(chunks)} FIXED spoken beats "
            f"(do not reword/merge/split/reorder/skip any):\n{numbered}"
        )
        if prior_repair:
            user += f"\n\nThe previous output FAILED validation: {prior_repair}\nFix and resend."
        raw = anthropic_message(self.spec, system=_SEG_SYS, user=user)
        segs = _parse_segments_json(raw)
        # Normalize start/end early — free-tier fallback models often emit
        # "m:ss" strings where Claude emits floats. _repair_chapter_timestamps
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
            chunks = split_spoken_chunks(text)
            cache_file = ch_cache / f"{ci}.json"
            if cache_file.exists():
                try:
                    segs = json.loads(cache_file.read_text(encoding="utf-8"))
                    # stale cache from before spoken_text existed, from a
                    # different chunking, or from the brief window where
                    # text_overlay was a condensed caption instead of
                    # mirroring spoken_text (broke caption sync/highlight) ->
                    # fall through and regenerate, never trust it silently.
                    if (len(segs) == len(chunks)
                            and all(s.get("spoken_text") for s in segs)
                            and all(s.get("text_overlay") == s.get("spoken_text")
                                    for s in segs)):
                        _log(f"stage2: chapter {ci}/{n} [{label}] "
                             f"(cached, skipping API call)")
                        all_segs.extend(segs)
                        continue
                    _log(f"stage2: chapter {ci}/{n} [{label}] cache stale "
                         f"(pre-fix format) — regenerating")
                except Exception:
                    pass
            repair_note = None
            for attempt in range(3):  # G3 per-chapter retry
                try:
                    segs = self._anthropic_chapter(label, chunks, repair_note)
                except Exception as e:
                    repair_note = str(e)[:300]
                    _log(f"stage2: chapter {ci}/{n} [{label}] "
                         f"attempt {attempt + 1}/3 failed: {str(e)[:120]}")
                    continue
                if len(segs) != len(chunks):
                    repair_note = (
                        f"you returned {len(segs)} objects but there are "
                        f"{len(chunks)} fixed spoken beats — return exactly "
                        f"{len(chunks)} objects, one per beat, same order."
                    )
                    _log(f"stage2: chapter {ci}/{n} [{label}] "
                         f"attempt {attempt + 1}/3: {repair_note}")
                    continue
                for si, (s, chunk) in enumerate(zip(segs, chunks), 1):
                    s["id"] = f"c{ci}_seg{si}"
                    # authoritative; never LLM-authored. text_overlay MIRRORS
                    # spoken_text exactly (never condensed) — the caption
                    # renderer (fx/text.py) word-times captions against the
                    # forced-alignment of the spoken audio, so caption tokens
                    # and aligned words must be the same set or sync/emphasis
                    # silently breaks (a shortened caption alone caused both
                    # to fail: fewer caption tokens than aligned words falls
                    # back to proportional spacing, and emphasis words picked
                    # from the full beat stop matching a caption that no
                    # longer contains them).
                    s["spoken_text"] = chunk
                    s["text_overlay"] = chunk
                cache_file.write_text(json.dumps(segs), encoding="utf-8")
                all_segs.extend(segs)
                _log(f"stage2: chapter {ci}/{n} [{label}] -> "
                     f"{len(segs)} segment(s)")
                break
        if not all_segs:
            raise RuntimeError("stage2: no segments produced from any chapter")
        _repair_chapter_timestamps(all_segs, chapter_specs)

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
