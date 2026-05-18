# 08 — Other Technical Gaps / Risks

Same class of "looks fine until you build it" gaps as the Google
word-timing one. Severity: 🔴 must solve before/at build · 🟡 handle during
build · 🟢 monitor.

## 🔴 G1 — Planned timestamps ≠ real TTS length (biggest one)
Claude invents `start`/`end` per segment (e.g. 41.0–44.5s), but Gemini's
actual spoken duration will differ every time. The whole schema has hard
timestamps; audio is variable-length.
**Fix:** **audio is the source of truth.** After TTS + silence pacing,
measure each segment's real duration, then *re-flow* the timeline; JSON
times become **targets/ordering hints, not absolutes**. Visuals are built
against measured audio, never the planned numbers. (This changes how the
renderer reads [04](04-json-schema.md) — noted there.)

## 🔴 G2 — No thumbnail / title / metadata generation
[01](01-product-strategy.md) says the thumbnail is ~60% of success, yet the
pipeline only outputs a video. This is the **biggest revenue gap**.
**Fix:** add a metadata stage — Claude proposes thumbnail concept + best
source frame + overlay text; Pillow composes a 1280×720 thumbnail; also
generate title variants, description, tags, chapters.

## 🔴 G3 — Claude JSON at scale (truncation/inconsistency)
A 6-min video ≈ 60–70 segments of dense JSON — a single Opus call can
truncate or drift, and must still pass the validator.
**Fix:** generate **per chapter**, schema-constrained, with a
validate-and-repair retry loop; assemble chapters into the full JSON.

## 🟡 G4 — Pexels relevance for abstract concepts
"betrayal shadow face" often returns irrelevant/cheesy stock; coverage gaps
for psychological abstractions.
**Fix:** query expansion + multi-candidate ranking; fallback to atmospheric
textures; heavy grade/blur so generic footage reads as *mood*, not literal;
small curated local fallback pool.

## 🟡 G5 — Pexels rate limits / quota
Free tier ~200 req/hr, ~20k/mo; multi-candidate × 60 segments × 12 videos
adds up. **Fix:** disk cache by video ID, candidate cap, backoff, reusable
local clip pool.

## 🟡 G6 — Render performance & cost
Per-frame Pillow text + zoom + LUT + grain + overlays at 1080p30 for 6 min
is very slow under MoviePy. **Fix:** push effects to FFmpeg filters where
possible, frame/asset caching, segment-parallel rendering, a fast
preview-quality mode; set a realistic per-video render-time budget.

## 🟡 G7 — Channel-level sameness
A single video varies well (10 layers), but the same fonts/LUTs/music every
video makes the *channel* feel samey. **Fix:** per-video rotating asset
palettes / seeds.

## 🟡 G8 — Always-on captions vs selective overlays
Most YouTube viewing is muted; `text_overlay` is currently selective, not
full captions. **Fix:** decide on an always-on word-synced caption track
(driven by forced alignment) layered under the stylized overlays.

## 🟡 G9 — Forced-alignment failure modes
TTS may add breaths/laughs or skip words; digit vs word ("7"/"seven")
mismatches drift alignment. **Fix:** text normalization before alignment,
per-segment alignment-confidence check, proportional-timing fallback.

## 🟢 G10 — Loudness target
YouTube normalizes to ~-14 LUFS; mastering must target that (2-pass
`loudnorm`) or dynamics get squashed. Avoid pumping from music ducking.

## 🟢 G11 — Policy / disclosure
Dark-psychology content risks limited-ads; YouTube requires
**synthetic/altered media disclosure** for AI voice + AI assembly; keep
license provenance (`render_manifest.json`). **Fix:** content-guidelines
pass + auto-set the "altered content" label + provenance log.

## 🟢 G12 — Gemini TTS reliability/filtering
Preview model: rate limits, occasional refusal/odd reads on words like
"manipulate"/"threat". **Fix:** retry + multi-take + TTS-only safe
rephrase (on-screen text unchanged).

## 🟢 G13 — No automated QA gate
A bad render is only caught by watching. **Fix:** automated checks —
duration match, no long black/silent spans, sync spot-check, manifest.

## 🟡 G15 — YouTube upload: OAuth + forced-private until verified
`videos.insert` needs **OAuth 2.0** (not an API key). Worse: until the
OAuth app passes Google's verification/audit, **every API upload is forced
`privacyStatus=private`** regardless of request — it cannot be made public
via API.
**Fix:** upload `private` + full metadata/thumbnail automatically; user
flips to public (or schedules) until the app is verified; document the
one-time OAuth consent + Cloud Console setup. Quota: 1600 units/upload of
10k/day default → fine for 12/mo.

## 🟢 G14 — Per-video cost unknown
Gemini TTS (preview pricing) + Opus tokens + storage not yet estimated.
**Fix:** add a cost line to `render_manifest.json` and a rough pre-build
estimate.

---

### Needs a decision now (folded into the build-readiness questions)
- G1 timeline model (audio-as-truth) — **recommended default, confirm**.
- G2 thumbnail/metadata — **in scope now or video-only first?**
- G8 always-on captions — style decision.
Everything else is handled during build per the fixes above.
