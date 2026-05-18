"""Extract the worked example from planning/05-sample-case.md.

Used by (a) the schema smoke test and (b) the OFFLINE Stage-2 stub, so the
whole pipeline can run end-to-end without Anthropic keys against the exact
spec the build targets (planning/05).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import ROOT
from .schema.model import ControlDocument

SAMPLE_MD = ROOT / "planning" / "05-sample-case.md"


def _json_blocks(md: str) -> list[dict]:
    blocks = re.findall(r"```json\s*(.*?)```", md, re.DOTALL)
    return [json.loads(b) for b in blocks]


# beat_type -> (scene, delivery) the way a Stage-2 director pass would emit.
_BEAT_TTS = {
    "chapter_open": ("a title card moment, the topic named plainly",
                     "measured, authoritative, slight ominous lean"),
    "setup": ("a calm establishing moment before the turn",
              "even, observational, unhurried"),
    "list_rhythm": ("a quick montage of small intense moments",
                    "clipped, staccato, rising momentum"),
    "shift_to_cold": ("warmth draining out of the frame",
                      "softer, trailing off, a beat of doubt"),
    "revelation_cold": ("a comforting illusion breaking",
                        "slow, low pitch, cold and deliberate"),
    "explanation": ("a clinical breakdown of the mechanism",
                    "precise, flat, dissecting"),
    "deepening": ("the implication sinking in",
                  "quieter, heavier, deliberate"),
    "wisdom_contrast": ("a calm true statement against the lie",
                        "warm but firm, knowing"),
    "direct_address": ("speaking straight to the viewer",
                       "intense, low, accusatory, close"),
}


def _enrich(seg: dict) -> dict:
    """Fill tts_scene/tts_delivery if the sample omits them (Stage-2 would)."""
    scene, delivery = _BEAT_TTS.get(
        seg.get("beat_type", ""),
        ("a tense dark-psychology moment", "controlled, low, deliberate"),
    )
    seg.setdefault("tts_scene", scene)
    seg.setdefault("tts_delivery", delivery)
    return seg


def load_sample_document() -> ControlDocument:
    """Assemble the canonical single-doc form from the sample markdown."""
    blocks = _json_blocks(SAMPLE_MD.read_text(encoding="utf-8"))
    top = next(b for b in blocks if "video_meta" in b)
    seg_block = next(b for b in blocks if "segments" in b)

    doc = {
        "video_meta": top["video_meta"],
        "chapters": top["chapters"],
        "global_assets": top["global_assets"],
        "segments": [_enrich(s) for s in seg_block["segments"]],
    }
    return ControlDocument.from_dict(doc)


def sample_script_markdown() -> str:
    """The Stage-1 narration script text from the sample (offline Stage-1)."""
    md = SAMPLE_MD.read_text(encoding="utf-8")
    # the script lives between '## Script' and '## Full Video JSON'
    m = re.search(r"## Script.*?\n(.*?)\n## Full Video JSON", md, re.DOTALL)
    return (m.group(1) if m else "").strip()
