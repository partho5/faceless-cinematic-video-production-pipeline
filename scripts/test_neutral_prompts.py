"""Validate the new niche-neutral writer + tts_framing prompts (NO Anthropic).

Pre-launch test. Manually plays the role of:
  • the Stage-1 writer LLM (hand-crafted 30s script matching the new
    _SCRIPT_SYS_BASE hook craft + forbidden-opening rules)
  • the tts_framing LLM (hand-crafted five-field JSON matching the new
    schema, then packed into the two-string scene+context API)
…then hands BOTH to the real Gemini TTS to produce master.wav. No
Anthropic spend. No video render.

Topic deliberately picked OUTSIDE the dark-psychology niche (sourdough)
so we can hear whether the new voice direction actually adapts.

Usage: python scripts/test_neutral_prompts.py
Output: output/_test_neutral_prompts/{script.md, master.wav}
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vp.audio_util import duration_seconds, write_wav  # noqa: E402
from vp.config import OUTPUT, get_config  # noqa: E402
from vp.pipeline.master import build_master  # noqa: E402
from vp.pipeline.voice import KeyPool, VoiceStage, collect_keys  # noqa: E402
from vp.schema.model import Segment  # noqa: E402


TOPIC = "Why should a customer choose your product over competitors"
SLUG = "product-choice"


# ---------------------------------------------------------------------------
# Stage-1 writer output. Hand-crafted by Claude in the role of the writer
# LLM, given the new _SCRIPT_SYS_BASE (hook = 3-sentence shape: specific
# observation -> stake -> tease; no forbidden opening; specificity over
# drama; ~30s @ 150 WPM ≈ 75 words).
# ---------------------------------------------------------------------------
SCRIPT = """\
**[HOOK · 0:00–0:11]**

Your customer never compared your product to your competitor's.

By the time they opened that comparison tab, the decision was already made.

They weren't deciding. They were confirming.

**[SHIFT · 0:11–0:25]**

The real choice gets made months earlier — the moment they first encounter your name. If you're already in their mental shortlist when the need hits, you've won. If you're not, no feature comparison rescues you.

**[CLOSING · 0:25–0:30]**

Compete for the moment before they need you. That's where the sale lives.
"""

# ---------------------------------------------------------------------------
# tts_framing output. Hand-crafted by Claude in the role of the framing LLM
# given the new five-field schema, then packed into (scene, context) the
# way the live function does at llm.py.
# ---------------------------------------------------------------------------
SCENE = (
    "a quiet office, addressing a founder who has been agonizing over a "
    "competitor's pricing page"
)
CONTEXT = (
    "Register: a senior peer talking straight, not a consultant pitching — "
    "direct, unhurried, no jargon. "
    "Tone: knowing, calmly contrarian, grounded in experience not theory. "
    "Pacing: measured, with a deliberate pause after each reframe so the "
    "implication lands before the next sentence. "
    "Inflection: drop on the punchy claims ('the decision was already "
    "made', 'they were confirming'); lift on the reframes ('months "
    "earlier', 'before they need you')."
)


def _spoken_text(script_md: str) -> str:
    """Drop the **[NAME · m:ss–m:ss]** chapter markers; flatten paragraphs
    into one continuous read for a single TTS call.
    """
    lines = [ln.strip() for ln in script_md.splitlines()]
    lines = [ln for ln in lines if ln and not re.match(r"^\*\*\[", ln)]
    return " ".join(lines)


def main() -> int:
    cfg = get_config()
    out = OUTPUT / f"_test_neutral_prompts_{SLUG}"
    out.mkdir(parents=True, exist_ok=True)
    print(f"[test] topic: {TOPIC}", flush=True)

    (out / "script.md").write_text(SCRIPT, encoding="utf-8")
    print(f"[test] script -> {out/'script.md'}", flush=True)

    text = _spoken_text(SCRIPT)
    print(f"[test] spoken text ({len(text.split())} words):", flush=True)
    print(f"[test]   {text}", flush=True)

    vs = VoiceStage(cfg)
    vs.scene = SCENE
    vs.context = CONTEXT
    print(f"[test] scene:   {SCENE}", flush=True)
    print(f"[test] context: {CONTEXT}", flush=True)
    print(f"[test] voice:   {vs.voice} via {vs.spec.model}", flush=True)
    print(f"[test] highly_emotional: {vs.highly_emotional}", flush=True)

    # Suffix output filenames by emotional-flag state so an A/B pair
    # (master.wav + master_emotional.wav) can coexist for comparison.
    suffix = "_emotional" if vs.highly_emotional else ""

    pool = KeyPool(collect_keys(cfg, vs.spec))
    print(f"[test] gemini key pool: {pool.size} key(s)", flush=True)

    print("[test] calling Gemini TTS (one continuous read)…", flush=True)
    samples = vs._synth(pool, text)
    raw_dur = len(samples) / 24000
    print(f"[test] received {len(samples)} samples ({raw_dur:.2f}s)", flush=True)

    # Stash the raw TTS output as the (single) segment audio file, then
    # route through build_master so the result is properly engineered
    # (1-pole highpass rumble cut + soft compressor + voice_master_volume
    # gain), not just a raw PCM dump. No music, no SFX, no Anthropic.
    audio_dir = out / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    seg_path = audio_dir / f"seg1{suffix}.wav"
    write_wav(seg_path, samples)
    print(f"[test] segment audio -> {seg_path}", flush=True)

    seg = Segment(
        id="seg1",
        beat_type="continuous_read",
        start=0.0,
        end=raw_dur,
        text_overlay=text,
        audio_path=str(seg_path),
        real_start=0.0,
        real_end=raw_dur,
        music_intensity=0.0,
    )
    video_meta = {
        "title": TOPIC,
        "voice_master_volume": 1.0,
        "music_master_volume": 0.0,
        "music_duck_amount": 0.7,
        "background_music_track": None,
    }

    master = out / f"master{suffix}.wav"
    print("[test] mastering (highpass + compressor + voice gain)…", flush=True)
    info = build_master([seg], master, video_meta)
    print(f"[test] mastered: {info['duration']:.2f}s, "
          f"{len(info.get('sfx_events', []))} sfx", flush=True)
    print(f"[test] DONE — NO Anthropic calls, 1 Gemini TTS call",
          flush=True)
    print(f"[test]   script: {out/'script.md'}", flush=True)
    print(f"[test]   master: {master} ({duration_seconds(master):.2f}s)",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
