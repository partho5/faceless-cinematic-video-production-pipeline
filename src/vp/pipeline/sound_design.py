"""SoundDesigner — an LLM acting as a tasteful short-form video editor.

This is the real editorial sound-effects system (plan/build-sound-effects.md).
It runs as ONE dedicated LLM pass *after* the control doc is validated and the
voice is forced-aligned, and *before* `build_master`. It:

  1. loads the curated 20-effect catalog (closed vocabulary),
  2. asks a restraint-trained editor model where — if anywhere — a single
     sound genuinely sharpens the story (usually: almost nowhere),
  3. drops anything off-palette (safety #1: must-be-one-of-20, no
     substitution — silence beats a wrong sound),
  4. resolves each cue's anchor to an exact in-segment time using the
     forced-alignment word timings (so a hit can land on a punch word),
  5. writes the result onto `segment.sound_fx` for `build_master`, which
     gain-stages it strictly under the narration (safety #2).

Design rules that are NOT negotiable (plan §1): the model is the editor —
there is no per-segment cap, no minimum spacing rule, no "fill the gaps".
Restraint comes ONLY from the prompt. The timeline is never stretched or
shifted; this pass decides placement only. Any failure (offline, bad JSON,
API error) yields ZERO cues — a silent video is the safe stopgap, never a
crash and never a guessed sound.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import ASSETS, Config
from ..schema.model import ControlDocument, SoundFX
from ..schema.validator import validate_sfx_cues
from .align import Alignment, normalize_token


def _log(msg: str) -> None:
    # same '[vp] ' prefix as vp.run so it streams into the GUI log pane
    print(f"[vp] {msg}", flush=True)


# --------------------------------------------------------------- catalog ----
_CATALOG_PATH = ASSETS / "sfx" / "catalog.json"

# Editorial purpose per id — the canonical "when an editor reaches for it"
# text from plan §2. catalog.json carries only a terse description; THIS is
# what feeds the prompt palette (the owner: names are self-describing, no
# long descriptions beyond this short purpose).
_PURPOSE: dict[str, str] = {
    "whoosh-transition": "scene/topic change, hard cut",
    "whoosh-fast": "quick snappy cut, fast camera move",
    "impact-boom": "lands a heavy statement / title / number reveal",
    "sub-drop": "heavy realization, drop into a dramatic beat",
    "riser-buildup": "tension building *into* a reveal/climax",
    "swell-cinematic": "emotional rise, grand moment approaching",
    "ui-click": "precise callout, selection, tick of a point",
    "ui-pop": "text/element pops on screen, light \"aha\"",
    "notification-ding": "message/alert/idea ping, list item",
    "glitch": "disruption, error, \"something's wrong\", edgy cut",
    "typing": "\"I researched / let me show you\" beat",
    "camera-shutter": "freeze-frame a key moment, snapshot",
    "success-cash": "money, profit, a win payoff",
    "success-chime": "correct, achieved, positive confirmation",
    "negative-wrong": "myth-bust, mistake, \"that's wrong\"",
    "reaction-applause": "triumphant resolution / celebration",
    "sting-suspense": "shocking fact, suspense, ominous pause",
    "paper-flip": "section/chapter change",
    "heartbeat": "tension just before a reveal",
    "clock-tick": "urgency, time pressure, countdown",
}


def load_catalog() -> list[dict]:
    """The 20-effect catalog (list of entries). [] if unreadable."""
    try:
        data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def catalog_ids() -> set[str]:
    return {e.get("id") for e in load_catalog() if e.get("id")}


# --------------------------------------------------------------- prompt -----
def _palette_block() -> str:
    """`id — purpose` lines, only for ids that are actually in the catalog."""
    ids = catalog_ids()
    lines = [f"  {i} — {p}" for i, p in _PURPOSE.items() if i in ids]
    return "\n".join(lines)


# The product. Over-sprinkling is the ONLY failure mode and the prompt is its
# ONLY defense (plan §9): persona of restraint, the taste model, the closed
# palette, a hard self-check gate, worked good AND bad few-shots, and an
# output contract where "no sound" is explicitly the common, correct answer.
def _system_prompt() -> str:
    return f"""\
You are the sound editor behind some of the most-watched short-form videos \
on the internet. Your entire reputation is built on RESTRAINT — on the \
sounds you refuse to add. Amateurs decorate every cut; you let a video \
breathe and land one perfect hit where it matters.

YOUR JOB
Given a video's narration and its ordered segments, decide where — if \
anywhere — a single sound effect genuinely sharpens the STORY. For any given \
moment the default answer is: no sound. You are not scoring the video; you \
are placing rare, surgical punctuation.

HOW GREAT EDITORS USE SFX (internalize this):
1. Silence is the default. A sound must EARN its place. Most segments get \
nothing. A whole 60–90s video is typically 0–6 cues TOTAL — many excellent \
videos use zero. 3–6 perfect sounds beat 30.
2. It PUNCTUATES, never underscores. One precise hit on the meaningful \
instant (the cut frame, the punch word, the reveal). Never a bed, never \
filler on every beat.
3. It BREATHES. A sound is felt only because the moments around it are \
clean. Clustering kills impact.
4. The one canonical multi-sound move is BUILD → PAYOFF: riser-buildup into \
brief tension, then impact-boom or sub-drop on the reveal. Whooshes ride \
REAL hard cuts. Dings/pops mark a callout that actually matters. \
Stings/heartbeat precede a genuine shock.

THE PALETTE — you may choose ONLY these ids (nothing else exists; having 20 \
tools in the box is NOT a reason to use them):
{_palette_block()}

THE FAILURE MODE — READ TWICE
Over-use. A sound on most segments, a whoosh on every transition, a ding on \
every list item, a drop on every sentence. This is the unmistakable mark of \
an amateur; it makes a video feel cheap, loud and exhausting and trains the \
viewer to ignore your sounds. If your plan puts a sound in most segments, it \
is WRONG — delete until only the truly earned ones remain.

PER-MOMENT SELF-CHECK — apply to EVERY candidate moment. Emit the cue only \
if ALL THREE are clearly yes:
  (a) Is there a specific, nameable story instant here — a hard cut, a \
reveal, a punch word, a shock, a section turn — not merely "this segment \
exists"?
  (b) Would a world-class editor put a sound here, or is the silence \
actually stronger?
  (c) If I deleted this one sound, would the video be measurably worse?
Default to NO. "It wouldn't hurt" / "adds energy" is a NO. When unsure, omit.

SPACING: never place two cues within ~2s of each other UNLESS it is a \
deliberate build→payoff pair. Do not sound-effect consecutive segments.

GENERAL-PURPOSE: this works for any video type (tutorial, vlog, finance, \
commentary, cinematic, comedy, calm explainer). A calm, informational video \
may correctly warrant ZERO cues. Match the palette purpose to the actual \
story beat, not the topic.

────────────────────────  WORKED EXAMPLES  ────────────────────────
GOOD — a ~75s finance explainer, 18 segments. Editor emits 3 cues:
  • riser-buildup at the start of the segment that sets up the big number \
("anchor": segment_start) — building INTO the reveal.
  • impact-boom on the word that IS the number/payoff ("on_word") — the \
single biggest moment of the video, intensity hard.
  • success-cash on the one segment about the profit result ("on_word" on \
"profit"), intensity normal.
  The other ~15 segments: NOTHING. That restraint is exactly why the three \
hits land. (Reasons name the exact instant, e.g. "lands the core $1M \
reveal".)

GOOD — a ~60s personal-story commentary, 14 segments. Editor emits 2 cues:
  • sting-suspense on the word that opens the shocking turn ("on_word"), \
intensity normal — a genuine gut-punch line.
  • heartbeat at the start of the final pre-resolution beat \
(segment_start), intensity soft — quiet dread before the reveal.
  A whoosh on the intro cut and a ding on the takeaway were CONSIDERED and \
REJECTED as decorative. Zero cues would also have been defensible here.

BAD — never do this:
  ✗ A whoosh-transition (or whoosh-fast) on every one of 9 scene cuts. \
WRONG: predictable, exhausting, no individual cut earned it; great editors \
let the vast majority of cuts pass in silence.
  ✗ notification-ding on each of 6 list items + ui-pop on 5 more segments. \
WRONG: decorative underscoring, not punctuation; the viewer stops hearing \
it by item 2.
  ✗ A hard sub-drop over a hushed, emotional confession to "add weight". \
WRONG: it buries the line and misreads the moment — the silence was far \
stronger. Wrong taste, not just wrong level.
────────────────────────────────────────────────────────────────────

OUTPUT — return STRICT JSON and NOTHING else (no prose, no markdown):
{{"cues":[
  {{"segment_id":"<exact id from the SEGMENTS list>",
   "sfx_id":"<exactly one palette id>",
   "anchor":{{"kind":"segment_start|segment_end|on_word|at_fraction",
            "word":"<for on_word: an exact word copied from THAT \
segment's text>","occurrence":1,"fraction":0.5}},
   "intensity":"soft|normal|hard",
   "reason":"<concrete: name the exact story instant this sharpens>"}}
]}}
- on_word lands the sound on a spoken word — use it for emphasis hits; copy \
the word verbatim from that segment's text and set occurrence (1-based) if \
it repeats. segment_start / segment_end sit at the cut. at_fraction (0..1 \
through the segment) is an escape hatch only.
- intensity: soft = subtle texture under the voice; normal = a clear \
accent; hard = a major moment (a reveal/climax) — hard is rare.
- reason is MANDATORY and must name the specific instant. "adds energy", \
"feels good", "for emphasis" are NOT reasons — if that is all you have, \
OMIT the cue.
- An empty list — {{"cues":[]}} — is a correct, common, and often the BEST \
answer. Never invent cues to look thorough."""


def _user_prompt(doc: ControlDocument) -> str:
    title = str(doc.video_meta.get("title", "") or "")
    full = " ".join(
        s.spoken_text.strip() for s in doc.segments if s.spoken_text
    ).strip()
    segs = [
        {
            "id": s.id,
            "beat": s.beat_type,
            "cut_in": s.cut_in_type,
            "cut_out": s.cut_out_type,
            "dur_s": round(s.duration, 1),
            "text": (s.spoken_text or "").strip(),
        }
        for s in doc.segments
    ]
    return (
        f"VIDEO TITLE: {title}\n\n"
        f"FULL NARRATION (for arc/context only — do NOT add a sound just "
        f"because a line is dramatic):\n{full}\n\n"
        f"SEGMENTS (place cues by segment_id; for on_word pick a word that "
        f"appears in THAT segment's own text):\n"
        f"{json.dumps(segs, ensure_ascii=False)}"
    )


# ----------------------------------------------------- anchor resolution ----
def _resolve_anchor(
    anchor: dict, align: Alignment | None, seg_dur: float
) -> tuple[float, str | None]:
    """Anchor -> seconds from the segment audio start (+ optional note).

    Times are segment-audio-relative to match Alignment word times and what
    build_master expects (`starts[id] + timing`). on_word never throws: a
    missing/unfound word falls back to segment_start (plan §4.2, §9).
    """
    kind = (anchor or {}).get("kind", "segment_start")
    hi = max(0.0, seg_dur)

    if kind == "segment_start":
        return 0.0, None
    if kind == "segment_end":
        return hi, None
    if kind == "at_fraction":
        try:
            f = float((anchor or {}).get("fraction", 0.5))
        except (TypeError, ValueError):
            f = 0.5
        return max(0.0, min(1.0, f)) * hi, None
    if kind == "on_word":
        word = str((anchor or {}).get("word", "")).strip()
        try:
            occ = max(1, int((anchor or {}).get("occurrence", 1)))
        except (TypeError, ValueError):
            occ = 1
        if align and align.words and word:
            toks = [t for t in (normalize_token(w) for w in word.split()) if t]
            if len(toks) == 1:
                seen = 0
                tgt = toks[0]
                for w in align.words:
                    if normalize_token(w.word) == tgt:
                        seen += 1
                        if seen == occ:
                            return max(0.0, min(w.start, hi) if hi else w.start), None
            elif len(toks) > 1:
                span = align.find(word)
                if span:
                    return max(0.0, min(span[0], hi) if hi else span[0]), None
        return 0.0, f"on_word {word!r} not found -> segment_start"
    return 0.0, f"unknown anchor kind {kind!r} -> segment_start"


# --------------------------------------------------------- the pass ---------
class SoundDesigner:
    """One restraint-trained editorial LLM pass; mutates segment.sound_fx."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.spec = cfg.model("sound_design")

    def _call_llm(self, doc: ControlDocument) -> list[dict]:
        """Raw cues from the model. [] on offline / any failure (silent)."""
        if self.spec.offline:
            _log("sound design: offline -> no SFX (silent, safe stopgap)")
            return []
        try:
            from ..llm import anthropic_message  # lazy

            raw = anthropic_message(
                self.spec,
                system=_system_prompt(),
                user=_user_prompt(doc),
            )
            blob = raw[raw.find("{"): raw.rfind("}") + 1]
            data = json.loads(blob) if blob else {}
            cues = data.get("cues", []) if isinstance(data, dict) else data
            return cues if isinstance(cues, list) else []
        except Exception as e:  # never break the pipeline; silence is safe
            _log(f"sound design: LLM/parse failed ({str(e)[:120]}) -> no SFX")
            return []

    def design(self, doc: ControlDocument, aligns: dict) -> dict:
        """Pick + resolve cues, write them onto segments, return a summary.

        SoundDesigner OWNS segment.sound_fx: every segment is cleared first
        so legacy/sample-doc `sound_fx` can never leak past this pass
        (Stage-2 no longer emits it; plan §5).
        """
        for s in doc.segments:
            s.sound_fx = []

        allowed = catalog_ids()
        raw = self._call_llm(doc)
        cues, warns = validate_sfx_cues(raw, allowed)
        for w in warns:
            _log(f"sound design: {w}")

        by_id = {s.id: s for s in doc.segments}
        placed: list[dict] = []
        for c in cues:
            sid = c["segment_id"]
            seg = by_id.get(sid)
            if seg is None:
                _log(f"sound design: cue -> unknown segment {sid!r}, skipped")
                continue
            al = aligns.get(sid)
            seg_dur = 0.0
            if seg.audio_path:
                try:
                    from ..audio_util import duration_seconds

                    seg_dur = duration_seconds(Path(seg.audio_path))
                except Exception:
                    seg_dur = 0.0
            at_s, note = _resolve_anchor(c.get("anchor"), al, seg_dur)
            if note:
                _log(f"sound design: {sid}/{c['sfx_id']}: {note}")
            seg.sound_fx.append(SoundFX(
                type=c["sfx_id"],
                timing=round(float(at_s), 4),
                intensity=c.get("intensity", "normal"),
                reason=str(c.get("reason", "")),
            ))
            placed.append({
                "segment_id": sid,
                "sfx_id": c["sfx_id"],
                "intensity": c.get("intensity", "normal"),
                "at_s": round(float(at_s), 3),
                "reason": str(c.get("reason", "")),
            })

        n_in = len(raw) if isinstance(raw, list) else 0
        return {
            "n_cues": len(placed),
            "n_dropped": max(0, n_in - len(placed)),
            "offline": bool(self.spec.offline),
            "model": self.spec.model,
            "cues": placed,
            "warnings": warns,
        }
