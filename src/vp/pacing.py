"""Engineered narration pacing (planning/06 §5).

The control JSON *may* specify pre/post_silence_ms per segment. When it
doesn't (0), we still need a real edit's breathing room rather than
wall-to-wall speech, so we derive sensible gaps from the beat type and the
line's end punctuation. Explicit non-zero JSON values are always respected
(intentional direction wins over defaults).
"""
from __future__ import annotations

# Claude's beat vocabulary varies run to run, so match by KEYWORD, not an
# exact set. Substring hit -> that dramatic treatment.
_HOLD_KW = ("hook", "open", "reveal", "realiz", "deep", "address", "shift",
            "wisdom", "climax", "shock", "confess", "pause", "thesis",
            "promise", "punch", "turn", "twist", "warning")
_LEADIN_KW = ("reveal", "realiz", "address", "shift", "shock", "confess",
              "twist", "turn", "warning")


def _hit(beat: str, kws: tuple[str, ...]) -> bool:
    return any(k in beat for k in kws)


def effective_silences(seg) -> tuple[int, int]:
    """-> (pre_ms, post_ms) actually used for this segment."""
    pre = int(seg.pre_silence_ms or 0)
    post = int(seg.post_silence_ms or 0)
    # NOTE: with continuous per-chapter synthesis (pipeline/voice.py) the
    # natural pauses are already IN the audio. So default = NO inserted
    # silence (inserting dead air on top sounds robotic). pre/post here are
    # used only when the director JSON explicitly sets them for a rare,
    # deliberate extra hold beyond what the model produced.
    return min(pre, 2999), min(post, 2999)
