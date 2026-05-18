# Plan: Editorial Sound-Effects System

> Self-contained build plan. A fresh session should be able to execute this
> from this file + the repo, with no prior conversation. Read it fully before
> coding. Use maximum reasoning; the hard part is taste, not plumbing.
>
> **RESUMING?** Go to **§10 Build progress log** first. It is the single
> source of truth for what is done vs. pending — trust it over assumptions,
> and **update it in this file after every meaningful step** so the next
> session (or a crash-recovered one) can pick up exactly where this left off.

---

## 0. One-paragraph summary

The pipeline used to let the LLM emit **free-form** `sound_fx` names; an old
procedural synthesizer then guessed audio from the name by substring match,
collapsing dozens of invented names (anything containing `swell`/`whoosh`)
onto one identical white-noise burst — videos sounded like broken TV static.
That synth was deleted; SFX is now **load-only and silent if the named file
is absent** (safe but lifeless — silence is a stopgap, not the design). We
have since curated a clean **20-effect library** with a catalog. This plan
builds the real system: an **LLM acting as a tasteful short-form editor** that
picks **only from those 20** (or, usually, **nothing**) and places a sound
**only where it genuinely sharpens the story** — like a well-edited influencer
video — with two non-creative safeties: the pick must be one of the 20, and a
sound can never bury the narration.

---

## 1. Locked decisions (do not relitigate)

These were settled with the product owner. Treat as requirements.

1. **Closed vocabulary.** The LLM may only choose from the 20 catalog ids.
   It **must be able to choose none**, and should choose none for most
   scenes. Unknown / off-list id ⇒ **no effect** (NOT nearest-category, NOT
   synthesis). Silence beats a wrong sound.
2. **LLM is the editor.** *When* a sound is used, *which* one, *where* in the
   scene, and *how often* are **100% the LLM's editorial judgement**. There is
   **no mathematical rule**: no per-segment cap, no minimum spacing, no
   "inject where there's a gap". Placement is driven by story meaning (a cut,
   a reveal, a punch word, a section change), exactly like a human editor.
3. **Two non-creative safeties only** (these are plumbing, not taste):
   - **must-be-one-of-20** — anti-hallucination membership check.
   - **can't-bury-voice** — SFX is gain-staged to always sit *under* the
     narration. Absolute requirement.
4. **Must not spoil the seamless audio.** Do **not** stretch/shift the
   timeline to make room for a sound. Do not insert silence. SFX rides on the
   existing mastered timeline.
5. **No procedural synthesis, ever.** Removed on purpose. Do not bring it
   back in any form.
6. **General-purpose.** The product is no longer dark-psychology-specific. The
   palette and prompt must work for any video type (tutorial, vlog, finance,
   commentary, cinematic, comedy, etc.).
7. **Taste lives in the prompt.** Influencer-grade restraint comes from the
   editor persona + the palette's stated purpose + worked few-shot examples
   (good sparse usage AND bad over-sprinkled counter-examples) + a forced
   per-scene self-check *"is silence stronger here?"* with default bias = no
   sound. There is no algorithmic taste enforcement by design.

Out of scope: ambient beds/loops (the 20 are all short one-shots); music
redesign; the parked grain/camera/cost work (already merged — see §8).

---

## 2. The curated library (DONE — do not regenerate)

`assets/sfx/` contains 20 cleaned WAVs + `assets/sfx/catalog.json`. They were
decoded to mono @ project sample-rate, **silence-trimmed on the real signal**
(head/tail dead air removed by envelope detection, not blind cuts),
length-capped per category only when the true sound exceeded the target,
micro-faded to avoid clicks, and peak-normalized to ~−1 dBFS.

`assets/SFX-Sound-Effects/` is the **untouched 225-file backup** — pull
alternate takes from here if a chosen sound is later rejected.

`catalog.json` entry shape:
```json
{ "id": "impact-boom", "file": "impact-boom.wav", "category": "impact",
  "description": "cinematic boom hit", "duration_s": 2.5,
  "peak_dbfs": -1.0, "source_original": "mixkit-big-cinematic-impact-788.mp3" }
```

The 20 ids, category, and **editorial purpose** (this purpose text should feed
the prompt palette):

| id | category | editorial purpose (when an editor reaches for it) |
|---|---|---|
| whoosh-transition | whoosh | scene/topic change, hard cut |
| whoosh-fast | whoosh | quick snappy cut, fast camera move |
| impact-boom | impact | lands a heavy statement / title / number reveal |
| sub-drop | sub_drop | heavy realization, drop into a dramatic beat |
| riser-buildup | riser | tension building *into* a reveal/climax |
| swell-cinematic | swell | emotional rise, grand moment approaching |
| ui-click | ui | precise callout, selection, tick of a point |
| ui-pop | ui | text/element pops on screen, light "aha" |
| notification-ding | notification | message/alert/idea ping, list item |
| glitch | glitch | disruption, error, "something's wrong", edgy cut |
| typing | typing | "I researched / let me show you" beat |
| camera-shutter | camera | freeze-frame a key moment, snapshot |
| success-cash | success | money, profit, a win payoff |
| success-chime | success | correct, achieved, positive confirmation |
| negative-wrong | negative | myth-bust, mistake, "that's wrong" |
| reaction-applause | reaction | triumphant resolution / celebration |
| sting-suspense | sting | shocking fact, suspense, ominous pause |
| paper-flip | paper | section/chapter change |
| heartbeat | heartbeat | tension just before a reveal |
| clock-tick | clock | urgency, time pressure, countdown |

---

## 3. How well-edited influencer videos use SFX (the taste model)

Encode this understanding into the prompt. Three principles:

1. **Silence is the default.** A sound must *earn* its place. The large
   majority of scenes get **nothing**. A video with 3–6 perfectly placed
   sounds beats one with 30.
2. **It punctuates, never underscores.** One precise hit on the meaningful
   instant (the cut frame, the punch word, the reveal). Never a continuous
   bed, never decorative filler on every beat.
3. **It breathes.** A sound is felt because the moments around it are clean.
   Clustering kills impact.

Canonical move: **riser-buildup → (brief tension) → impact-boom/sub-drop** on
the reveal — the build-and-payoff. Whooshes ride real cuts/transitions.
Dings/pops mark on-screen callouts. Stings/heartbeat precede shocks.

---

## 4. Architecture to build

### 4.1 Where selection happens — **new dedicated `SoundDesigner` pass** (recommended)

Two options were considered:

- **(a)** Extend the Stage-2 segmentation prompt (`_SEG_SYS` in
  `src/vp/pipeline/script_gen.py`) with an optional `sfx` field per segment.
- **(b)** A separate `SoundDesigner` LLM pass after the control doc is
  validated and the voice is aligned. **← choose this.**

Rationale for (b): a real editor decides SFX while watching the **whole cut**,
not chapter-by-chapter; restraint needs a focused prompt with rich few-shot
examples that would bloat the already-large `_SEG_SYS`; and it can use the
**forced-alignment word timings** (already produced by the voice stage) to
place a sound exactly on a punch word. Cost: one extra LLM call per video
(~$0.02–0.05 on Sonnet; fine — can run on the cheaper dev model too).

Pipeline insertion point (see `src/vp/run.py` `run()`): after Stage-2
validation and after the `VoiceStage` produces per-segment alignments
(`aligns`), before/at `build_master`. The SoundDesigner consumes: full script
text, the ordered segment list (id, text, beat_type, cut types, duration), the
per-segment word alignments, and the 20-item palette. It returns a **sparse**
plan.

### 4.2 SoundDesigner output schema

```json
{ "cues": [
  { "segment_id": "c2_seg3",
    "sfx_id": "impact-boom",
    "anchor": { "kind": "on_word", "word": "everything", "occurrence": 1 },
    "intensity": "hard",
    "reason": "lands the core reveal of the video" }
] }
```
- `anchor.kind ∈ {segment_start, segment_end, on_word, at_fraction}`.
  - `on_word` → resolve time from that segment's alignment word list
    (`aligns[segment_id].words[i].start`). If the word isn't found, fall back
    to `segment_start`.
  - `at_fraction` → `fraction ∈ [0,1]` of the segment duration (escape hatch).
- `intensity ∈ {soft, normal, hard}` → maps to a gain target (§4.4).
- `reason` is required (forces the model to justify ⇒ fewer gratuitous cues;
  log it, don't act on it). Empty/weak reason is acceptable to keep but the
  prompt should treat "no strong reason ⇒ omit the cue".
- An **empty `cues` list is the expected common output for calm videos** and
  must be explicitly encouraged in the prompt.

### 4.3 Validation (`must-be-one-of-20`)

In a new validator (or extend `src/vp/schema/validator.py`):
- Drop any cue whose `sfx_id` is not a catalog id (log it; **no substitution**).
- Clamp `intensity` to the enum (default `normal`).
- Resolve `anchor` → absolute time on the mastered timeline using the segment
  start offsets `build_master` already computes (`starts[s.id]`) + the
  alignment word time. Never move/resize segments.
- No count cap. (Restraint is the prompt's job, per §1.2. Optionally log the
  cue count to telemetry/manifest for observation only.)

### 4.4 Mixing & `can't-bury-voice` (the critical safety)

Implement in `src/vp/pipeline/master.py`. Current relevant facts:
- The mix is mono: `mix = voice + music`; music is already ducked under voice
  via an amplitude envelope; a safety limiter scales peaks >1; a final 2-pass
  `ffmpeg loudnorm` targets ~−14 LUFS.
- SFX is currently added as `clip * fx.volume` at a segment-relative time with
  a `rendered` bool logged and a `sfx_skipped_no_asset` count.

New rule (concrete starting point — tune during verification):
- Intensity → target SFX level **relative to local voice**, NOT absolute:
  - measure local voice RMS in a short window around the cue time (reuse the
    `_envelope` helper).
  - `soft  → SFX peak ≈ voice_local_RMS × 0.50` (~−6 dB under)
  - `normal→ SFX peak ≈ voice_local_RMS × 0.80`
  - `hard  → SFX peak ≈ voice_local_RMS × 1.10` **but** then hard-clamped so
    SFX peak never exceeds `voice_local_RMS × 1.25` AND never exceeds a global
    ceiling (e.g. 0.7 of full-scale pre-limiter).
  - If the cue lands in a natural voice gap (voice_local_RMS ≈ 0), fall back
    to an absolute target (e.g. peak 0.45 full-scale) so a sound placed on a
    deliberate pause is still audible but controlled.
- Optionally, briefly duck the **music** bed (not the voice) by ~3–4 dB for
  the SFX duration so the effect reads clean. This is mixing, allowed; it is
  NOT a placement rule and does not alter timing.
- Keep the existing safety limiter and final loudnorm untouched.

The assets are already peak-normalized to −1 dBFS, so relative scaling is
predictable. Verify by ear/measurement that narration intelligibility is
never reduced (acceptance §6).

### 4.5 Prompt (the actual product — invest the most reasoning here)

Compose the SoundDesigner system prompt with:
1. **Persona:** a world-class short-form / influencer video editor known for
   restraint and impact.
2. **The palette:** the 20 ids + their editorial purpose (table in §2). Names
   are self-describing; per the owner, no separate long descriptions are fed
   beyond the short purpose.
3. **Principles** from §3 verbatim in spirit (silence is default; punctuate
   don't underscore; let it breathe; build→payoff).
4. **Few-shot examples** — at least 2 GOOD (a 60–90s script → only 3–5 cues,
   well justified) and 2 BAD counter-examples (over-sprinkled / decorative /
   burying dialogue) explicitly labelled as what NOT to do.
5. **Per-scene self-check** instruction: for each candidate moment, ask "would
   a top editor put a sound here, or is silence stronger?" — default to no
   sound; only emit a cue when the answer is clearly yes.
6. **Output contract:** strict JSON per §4.2; empty `cues` is valid and
   common; every cue needs a concrete `reason`.

Model: prod default `claude-sonnet-4-6`; honor `VP_LLM_MODEL` override (dev
uses `claude-haiku-4-5-20251001`). Route the call through
`src/vp/llm.py:anthropic_message` so the **cost tracker auto-records it**
(tag purpose e.g. `sound_design`). Add the purpose to `config.yaml` models if
a dedicated model entry is wanted, else reuse `segmentation_direction`'s spec.

---

## 5. File-by-file change map

- `src/vp/pipeline/sound_design.py` **(new)** — catalog loader; SoundDesigner
  LLM call; prompt; output parse; cue→time resolution using alignments.
- `src/vp/schema/model.py` — represent resolved cues (extend/replace the
  free-form `SoundFX`: keep `type` but it now must be a catalog id; add
  `intensity`; `timing` becomes the resolved absolute-in-segment seconds).
- `src/vp/schema/validator.py` — membership check (drop non-catalog ids,
  no substitution), intensity clamp.
- `src/vp/pipeline/script_gen.py` — **remove** the `sound_fx` instruction from
  `_SEG_SYS` (Stage-2 no longer invents SFX; it's the SoundDesigner's job).
  Legacy `sound_fx` entries in the sample/control docs: ignore or strip.
- `src/vp/pipeline/master.py` — replace the current `fx.volume` add with the
  intensity→relative-gain model + can't-bury-voice clamp + optional music
  duck under SFX; keep `rendered`/`sfx_skipped` logging; add chosen-cue log
  (id, time, intensity, reason).
- `src/vp/run.py` — call SoundDesigner after Stage-2 validation + voice
  alignment, feed its cues into `build_master`; add `_log` progress lines
  (consistent with existing `[vp] ...` streaming the GUI relies on); include a
  cue summary + count in the manifest.
- `src/vp/config.py` / `config.yaml` — optional `sound_design` model purpose.
- `tests/` — see §6.

Do not regenerate `assets/sfx/` or `catalog.json`.

---

## 6. Tests & acceptance criteria

Unit tests (extend `tests/`, pure-Python, no network — mock the LLM):
- Catalog loads; 20 ids; files exist.
- Validator drops an off-catalog id with no substitution and logs it.
- `anchor=on_word` resolves to the aligned word's start; missing word →
  `segment_start` fallback.
- `can't-bury-voice`: with a synthetic loud SFX over a known voice RMS, the
  post-mix SFX peak ≤ the clamp ceiling; narration RMS in that window is not
  reduced below a threshold.
- Empty `cues` → master identical to no-SFX path (byte-stable).

End-to-end verification (manual, varied topics — run 2–3 short clips):
```
PYTHONPATH=src .venv/bin/python3 -m vp.run "<topic>" \
    --preset final --minutes 1 --segments 4 --approve --no-upload
# dev-cheap: prefix VP_LLM_MODEL=claude-haiku-4-5-20251001
```
Acceptance:
- Most segments have **no** SFX; cues are few and land on real story beats
  (inspect the logged `reason`s).
- Every chosen `sfx_id` ∈ the 20; no synthesis; unknowns silently dropped.
- Listening test: narration is always clearly above any SFX; no whoosh-spam;
  audio feels seamless (no timeline stretch; QA `no_long_silence` still OK).
- File size sane (the grain fix already removed the bitrate bomb; SFX adds
  negligible size).
- `llm_cost.json` shows the extra `sound_design` call accounted.

---

## 7. Definition of done

LLM-as-editor SoundDesigner pass is wired in; it picks only from the 20 and
usually picks nothing; cues are validated (membership + intensity), resolved
to exact times via alignment, and mixed with a hard can't-bury-voice clamp
without touching the timeline; Stage-2 no longer invents SFX; tests pass;
verification clips show tasteful, sparse, influencer-grade placement; cost
tracked; nothing regresses (grain/camera/size/QA all still good).

---

## 8. Environment & repo context (for the fresh session)

- Run pipeline: `PYTHONPATH=src .venv/bin/python3 -m vp.run "<topic>"
  --preset final|preview --minutes N [--segments N] --approve --no-upload`.
  GUI: `python run.py` (premium Tkinter; streams `[vp] ...` log lines and a
  progress bar — keep emitting `[vp]` lines for new stages).
- Models: prod default **claude-sonnet-4-6**; dev override env
  **`VP_LLM_MODEL=claude-haiku-4-5-20251001`** (scoped to script/segmentation;
  add `sound_design` to that override list if you create the purpose).
- All Anthropic calls go through `src/vp/llm.py:anthropic_message`, which
  auto-records cost via `src/vp/cost.py` (writes `output/<slug>/llm_cost.json`
  + appends `output/llm_cost_ledger.jsonl`). Tag the new call's purpose.
- Gemini TTS uses a **rotation key pool** (`GEMINI_API_KEY_1..`); voice stage
  produces per-chapter audio AND **forced-alignment word timings**
  (`aligns[segment_id].words[i].start/end`) — reuse these for `on_word`.
- `build_master` (`src/vp/pipeline/master.py`) computes per-segment start
  offsets `starts[s.id]` on the reflowed timeline — use these to place cues.
- Already merged, do NOT redo: grain default off + clamped (`fx/color.py`);
  Ken Burns / camera motion strengthened (`fx/ambient.py`, `fx/camera.py`);
  config rotation-aware offline check; `--hint`/Script-Hints UI; Stage-1/2
  progress logging; cost tracker; SFX procedural synth REMOVAL + silent
  fallback in `master.py` (this plan replaces the silent stopgap with the
  real editorial system).
- Parked, unrelated: full-length multi-topic verification renders; a louder
  "sample-doc fallback" warning banner. Not part of this plan.

## 9. Risks / judgement calls for the implementer

- **Over-sprinkling** is the main failure mode and is *only* defended by
  prompt quality. Spend real effort on §4.5 (persona, purpose, few-shot
  good/bad, self-check). If verification shows too many cues, strengthen the
  examples/self-check — do NOT add a numeric cap (owner explicitly rejected
  mechanical rules).
- **on_word placement** depends on alignment quality; always have the
  `segment_start` fallback so a bad match never throws.
- **can't-bury-voice numbers** in §4.4 are a starting point; tune by listening
  during verification, but the *invariant* (SFX strictly under narration) is
  non-negotiable.
- Keep the timeline immutable — placement only, never stretch/insert. This is
  what protects the "seamless" feel the owner cares about most.

---

## 10. Build progress log (UPDATE THIS AS YOU GO)

This section is **mutable state**, not reference. Every other section is
fixed spec; this one is the live checklist. Protocol:

- Before starting work, read the checklist below and the **Resume notes** to
  see what is done, in progress, or blocked.
- Mark a box `[x]` **only** when the step is implemented *and* its check
  passes (tests green / verified by the criterion next to it). Use `[~]` for
  started-but-incomplete.
- After each step, edit this file: flip the box, and append a one-line dated
  entry to the **Session log** (what changed, key file, any deviation from
  spec and why). Keep entries terse; this is a breadcrumb trail, not prose.
- If you deviate from the spec in §1–§9, record it in **Resume notes** so the
  next session doesn't "fix" it back.
- "Done" = every box `[x]` AND §7 Definition of done satisfied.

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done & checked.

### Checklist (ordered for dependency, not strict)

- [x] **S0** Read this whole file; confirm `assets/sfx/catalog.json` has 20
  ids and the WAVs exist (do NOT regenerate). — *check: catalog loads, 20 ids*
- [x] **S1** `src/vp/pipeline/sound_design.py` (new): catalog loader +
  SoundDesigner LLM call routed via `llm.py:anthropic_message`
  (purpose `sound_design`) + output parse + cue→time resolution from
  alignments. (§4.1, §4.2)
- [x] **S2** Prompt authored in `sound_design.py` per §4.5 (persona, palette
  w/ purpose, principles §3, ≥2 GOOD + ≥2 BAD few-shots, per-scene
  self-check, strict-JSON contract, empty-cues encouraged). — *the product;
  spend the most effort here*
- [x] **S3** `src/vp/schema/model.py`: resolved-cue representation (catalog-id
  `type`, `intensity`, resolved `timing`). (§5)
- [x] **S4** `src/vp/schema/validator.py`: must-be-one-of-20 drop (no
  substitution, log), intensity clamp, anchor→time resolution, no count cap.
  (§4.3)
- [x] **S5** `src/vp/pipeline/script_gen.py`: remove `sound_fx` from
  `_SEG_SYS`; strip/ignore legacy entries. (§5)
- [x] **S6** `src/vp/pipeline/master.py`: intensity→relative-gain +
  can't-bury-voice clamp + optional music-only duck; keep
  `rendered`/`sfx_skipped` logs; add chosen-cue log. Timeline immutable. (§4.4)
- [x] **S7** `src/vp/run.py`: call SoundDesigner after Stage-2 validation +
  voice alignment; feed cues to `build_master`; `[vp]` progress lines; cue
  summary+count in manifest. (§5)
- [x] **S8** `src/vp/config.py` / `config.yaml`: optional `sound_design` model
  purpose; honor `VP_LLM_MODEL` dev override. (§4.5)
- [x] **S9** Unit tests per §6 (catalog, off-id drop, on_word + fallback,
  can't-bury-voice clamp, empty-cues byte-stable). All green, no network.
- [x] **S10** E2E verification: 2–3 short clips, varied topics (§6 command).
  Acceptance §6 met (sparse, in-vocab, voice always on top, seamless, cost
  tracked). Record findings + any prompt tuning in Session log.
- [x] **S11** §7 Definition of done re-read and fully satisfied; nothing in §8
  "already merged" regressed.

### Resume notes (free-form: deviations, blockers, decisions)

Feature is **COMPLETE** (all S0–S11 done, 20/20 tests, §6 verified). Design
decisions a future session must NOT "fix" back:

- **Cue→time resolution lives in `sound_design.py`, not `validator.py`.**
  `validator.py:validate_sfx_cues()` is intentionally PURE (membership +
  intensity clamp only, no I/O) so it is trivially unit-testable; anchor
  resolution needs alignments + audio durations and sits in
  `sound_design.py:_resolve_anchor()`. This matches §5's split exactly — not
  a deviation.
- **SoundDesigner OWNS `segment.sound_fx`.** `design()` clears EVERY
  segment's `sound_fx` before attaching its picks, so legacy entries from
  the sample-doc offline fallback can never leak past this pass (chose
  "strip", robustly, over "ignore" — §5 allowed either).
- **Word timings are NOT dumped into the prompt.** The model names a punch
  word; the exact time is resolved locally from `aligns`. §4.1 lists
  alignments as a consumed input *for placement* — placement uses them; a
  leaner prompt is better for restraint + cost. Deliberate.
- **`SoundFX.volume` kept (vestigial).** Gain is now driven by `intensity`;
  `volume` stays only for back-compat with the existing validator range
  clamp + older tests. Don't rip it out.
- **§4.4 numbers used as-is** and verified holding on real content
  (sfx_peak ≤ min(1.25·voiceRMS, 0.70) for every cue across 3 runs).
- **Verification used the §6 dev-cheap path** (haiku, 3 reused real runs:
  bedtime/discipline/illusion) rather than fresh full renders: the SFX
  acceptance criteria are fully exercised by SoundDesigner + build_master on
  real segments+alignments; nothing in the SFX path touches video, so a
  full Pexels/moviepy re-render was correctly not re-done.

### Session log (append-only, newest at bottom)

- 2026-05-18 — Plan instrumented with self-tracking §10. No build code written
  yet; all boxes open. Next session starts at S0.
- 2026-05-18 — Baseline (§8 merged work + curated catalog + plan) committed on
  branch `sound-effects-system`; 235 MB raw SFX backup gitignored.
- 2026-05-18 — S3 model.py: `SoundFX` +`intensity`/+`reason`, parsed in
  `_segment_from_dict` (defaults keep legacy/roundtrip safe).
- 2026-05-18 — S4 validator.py: pure `validate_sfx_cues()` (drop off-catalog,
  no substitution, intensity clamp, no count cap).
- 2026-05-18 — S1+S2 sound_design.py: catalog loader, §2 purpose map, the
  restraint prompt (persona/palette/principles/3 BAD+2 GOOD few-shots/
  self-check/strict-JSON), LLM call (purpose `sound_design`), robust JSON
  parse, `_resolve_anchor` (on_word+occurrence / fallback / start/end/
  fraction). Offline/any-fail ⇒ 0 cues (silent, safe).
- 2026-05-18 — S8 config.{yaml,example.yaml} +`sound_design` (sonnet-4-6,
  temp 0.2); config.py override list +`sound_design`. S5 `_SEG_SYS`:
  removed `sound_fx`, added explicit "do NOT emit sound_fx".
- 2026-05-18 — S6 master.py: intensity→relative-gain vs local voice RMS +
  hard clamp (≤1.25·RMS & ≤0.70) + voice-gap 0.45 fallback + ~3.5 dB
  music-only duck; richer sfx_log (intensity/reason/voice_rms/sfx_peak) +
  `sfx_cues`. Timeline untouched.
- 2026-05-18 — S7 run.py: SoundDesigner wired after reflow, before
  build_master; `[vp]` per-cue logs; cue summary into runtime/manifest.
- 2026-05-18 — S9: tests/test_sound_design.py (7 cases incl. can't-bury-voice
  + byte-stable empty path + end-to-end strip/resolve). Full suite 20/20,
  no network, no regressions.
- 2026-05-18 — S10/S11: live verify (haiku, 3 varied real runs) — bedtime=0
  cues (restraint ✓), discipline=3, illusion=2, all in-vocab, justified,
  seamless, voice-protected (master authoritative numbers), cost tracked
  (~$0.003/video haiku). §7 DoD fully satisfied; §8 baseline not regressed.
  **DONE.**
- 2026-05-18 — Button/GUI E2E: ran the exact `vp.run` command the "Create
  Video" button issues (preview, prod sonnet, full TTS+render). Produced
  final.mp4, QA passed (incl. no_long_silence=0.00s), 1 tasteful cue
  (impact-boom, sfx_peak 0.141 ≤ ceil 0.161), manifest+cost recorded.
  Added GUI progress milestone `"sound design:" -> 66%` so the bar
  advances during the new stage. Button flow seamless incl. SFX.
