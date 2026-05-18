# 09 — Script Origin, Segmentation & Processing

Answers: *where does the original script come from, how is it produced,
split, and processed into the control-signal JSON?*

## The full chain

```
TOPIC (user: a title idea or angle)
   │
   ▼
[Stage 1 — Scriptwriter]  Claude Opus
   • writes the full narration script (like doc 05's ~900-word script)
   • structured into chapters: HOOK, SIGN 1..N, CLOSING
   • plain spoken English, no JSON yet
   → out/<slug>/script.md      ◄── REVIEW GATE (script = the whole video;
                                    user may edit/approve before spend)
   │
   ▼
[Stage 2 — Director / Segmenter]  Claude Opus, per chapter (gap G3)
   • takes the APPROVED script text
   • splits each chapter into 2–4s beats (segmentation rules below)
   • assigns beat_type + ALL control signals per segment
   • emits tts_scene / tts_delivery per segment
   → video.json + segments[]   ◄── schema validator (doc 04)
   │
   ▼
[TTS] Gemini per segment  →  [Forced align] stable-ts  →  [Master]
   │  (real audio length now known — timeline re-flows, gap G1)
   ▼
[Render] 10 variation layers → final.mp4  + [Metadata] thumbnail/title/desc
```

Two distinct Claude passes, not one: **writing** (creative, reviewable) is
separated from **directing** (mechanical control-signal assignment). The
script is the single highest-leverage artifact, so it gets its own review
gate before any TTS/render cost is incurred.

## Stage 1 — Scriptwriter pass

- **Input:** a topic/title (e.g. *"The 7 Signs Someone Is Quietly
  Manipulating You"*) — supplied by user, or proposed by Claude from the
  niche bank in [01](01-product-strategy.md).
- **Output:** `script.md` — narration only, chapter-marked, written for the
  niche voice (dark, second-person, knowledge-gap hooks, retention beats per
  [01](01-product-strategy.md)).
- **Length target:** ~150 spoken words/min → ~900 words ≈ 6 min.
- **Review gate:** user can edit `script.md` directly; Stage 2 only runs on
  the approved file. (Cheap to iterate here; expensive after TTS.)

## Stage 2 — Segmentation rules (script → beats)

Claude splits the approved script into segments using:

1. **Semantic/clause boundaries first** — never cut mid-clause; one idea per
   segment. Sentence or strong clause = natural segment edge.
2. **Target spoken length 2–4s** — estimate ≈ 2.5 words/sec, so ~6–10 words
   per segment; a long sentence becomes multiple segments, a punchy line
   stays whole.
3. **Beat typing** — each segment tagged (`hook`, `setup`, `revelation_cold`,
   `list_rhythm`, `direct_address`, …) which drives its visual/audio recipe
   (doc 03) and its `tts_delivery`.
4. **Emphasis extraction** — Claude marks the words to hit
   (`text_animation_emphasis[]`); these must stay within one segment.
5. **List detection** — rapid enumerations ("Constant texts. Endless
   compliments…") become a `rapid_clip_montage` segment with
   `montage_clips[]`.
6. **Durations are TARGETS only** — `start`/`end` are ordering hints. Real
   timing comes from TTS + forced alignment (gap G1); the renderer re-flows
   the timeline from measured audio. Claude is not expected to predict exact
   seconds.
7. **Per-chapter generation** — Stage 2 runs one chapter at a time to avoid
   truncation (gap G3); chapters are concatenated, IDs made unique
   (`s1_seg1` …), then validated as one document.

## What "processed" means downstream

Per segment, in order: build TTS prompt
(`persona_prefix` + `tts_scene` + `tts_delivery` + line) → Gemini audio →
forced-align the *known* segment text → measure real duration, apply
`pre/post_silence_ms` → master (loudness/de-ess) → that segment's true
timeline window is fixed → render engine applies the segment's control
signals (text anim on aligned word times, camera, LUT, cuts, SFX) → composite
→ mux → final MP4 + generated thumbnail/metadata.

## Inputs the user provides vs. system generates

| Thing | Source |
|-------|--------|
| Topic / title idea | **User** (or Claude proposes from niche bank) |
| Full narration script | Claude (Stage 1), **user-reviewable** |
| Segmentation + control JSON | Claude (Stage 2), auto-validated |
| Voice, clips, timing, render, thumbnail, metadata | System, fully automated |

> So: the "original script" in doc 05 was a *sample of Stage 1 output*. In
> production it's generated from just a topic, gated for human approval,
> then mechanically segmented and directed in Stage 2.
