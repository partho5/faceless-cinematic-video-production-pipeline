# 06 — Voice (Google Gemini TTS) & Word-Timing Strategy

Decided provider: **Google Gemini TTS** (validated via AI Studio).
Goal of this doc: squeeze maximum quality out of it, and fully solve the
two known Google gaps — **(A) no structured emotion params, (B) no
word-level timestamps ("per-wording lack")**.

---

## A. Maximizing Quality — concrete techniques

### 1. Per-segment scene + delivery prompting (replaces SSML emotion)
The AI Studio "scene" / "sample context" you liked are **prompt text**, not
API fields. Claude already knows each segment's `beat_type`/mood, so Claude
emits two strings per segment and the TTS module assembles:

```
Scene: {tts_scene}.
Delivery: {tts_delivery}.
Say: {line}
```

→ one Gemini call **per segment**. Result: delivery changes shot-to-shot
(cold reveal vs. clipped list vs. intimate confession) instead of one flat
read across the whole video. This alone is the biggest quality lever.

### 2. Locked voice persona, varied delivery
- Fix one `voice_name` for the entire video (timbre consistency).
- Keep a constant **persona prefix** ("A low, controlled male narrator,
  late-night documentary tone…") prepended to every segment so character
  stays stable while `tts_delivery` varies the emotion.

### 3. Multi-take + auto-select (cheap because Gemini ≪ ElevenLabs cost)
Generate 2–3 takes per segment, auto-pick the best by:
- duration closest to the segment's target window,
- loudness/energy matching the segment's `tension_level`,
- no truncation/artifact (silence-tail and clipping check).
Optional: keep all takes, let a review pass swap any later.

### 4. Cross-segment prosody continuity
Synthesizing per segment can cause flat seams. Mitigate:
- Pass the **previous line as unspoken context** in the prompt
  ("Previously said (do not speak): '…'. Now say: '…'") so intonation flows.
- Synthesize with ~150 ms head/tail handles and **crossfade** segment joins.

### 5. Engineered silence & pacing (Google won't "hold" tension)
Synthesize lines *without* relying on the model to pause. Python inserts
**exact** dramatic gaps between/within segments per JSON (`pre_silence_ms`,
`post_silence_ms`). Precise, repeatable control instead of hoping the model
breathes correctly.

### 6. Emphasis without SSML
Gemini has no `<emphasis>`. Three-layer fallback:
1. Put it in `tts_delivery`: "stress the words 'never' and 'strategy'."
2. The **visual** emphasis (Layer 2 text effects) already carries most
   perceived emphasis — audio doesn't have to do it alone.
3. Post: use the forced-aligned word region to apply a *subtle* gain/pitch
   nudge on that word if needed.

### 7. Pronunciation control
No phoneme SSML → use **respelled** tricky words in the TTS prompt text
(e.g., "narcissist → NAR-suh-sist") while the on-screen `text_overlay`
keeps correct spelling (they are separate fields anyway).

### 8. Universal mastering chain (polish, not fake-emotion)
Per segment, after synthesis: high-pass rumble cut → de-ess → gentle
compression → loudness normalize to **~-16 LUFS** (YouTube target) → peak
limit. Consistent, broadcast-clean voice across all 12 videos/month.

---

## B. Solving the "per-wording lack" (no word timestamps)

**The key insight:** we are NOT transcribing unknown audio — **we already
have the exact script text**. So this is a *forced alignment* problem, not
a speech-recognition problem. Forced alignment of known text to audio is
dramatically more accurate than ASR guessing.

### Approach (in priority order)

1. **WhisperX** (Whisper + wav2vec2 phoneme alignment) — practical default,
   ~10–30 ms word accuracy, easy Python integration.
2. **Montreal Forced Aligner (MFA)** — most accurate, gives **phoneme-level**
   timing (useful for sub-word emphasis & typewriter-per-char effects).
   Heavier setup; use if WhisperX accuracy is insufficient.
3. **whisper-timestamped / stable-ts** — lighter fallback.

### Why it's robust here
- Alignment runs **per segment** on short audio (2–6 s) → errors cannot
  accumulate across the video.
- Segment **start time comes from the JSON**; the aligner only supplies
  **within-segment word offsets** → add the two for absolute frame times.
- Known transcript means near-zero word-identity errors; only boundary
  precision matters, which alignment handles well.

### Pipeline position
```
Gemini TTS (segment audio) ─┐
segment text (from JSON) ────┴─► Forced Aligner ─► [{word, t_start, t_end}]
                                                    │
                                                    ▼
                              drives Layer-2 text animation:
                              word-by-word reveal, emphasis hits,
                              typewriter, highlight-dim, etc.
```

### Output contract (consumed by the render engine)
```json
{
  "segment_id": "s1_seg9",
  "words": [
    {"word": "If",        "start": 0.00, "end": 0.18},
    {"word": "they",      "start": 0.18, "end": 0.34},
    {"word": "god",       "start": 1.42, "end": 1.71},
    {"word": "month",     "start": 3.05, "end": 3.30},
    {"word": "six",       "start": 3.30, "end": 3.61}
  ]
}
```

Emphasis effects in the JSON reference words by string; the renderer matches
them to these aligned spans to fire `scale_up_glow`, `shake_red`, etc.
exactly on the spoken word.

> Net: Google's missing word timestamps are fully recovered — more
> accurately than `<mark>` or ElevenLabs would have given — because we
> exploit the known script via forced alignment.
