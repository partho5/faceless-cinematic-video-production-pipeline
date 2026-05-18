# 07 — Build Readiness Checklist

Note: you are in --dangerously-skip-permissions mode. so don't do anything that is potentially dangerous to the project.


use env, config (load creentials to config, and directly write non-credential config), 
venv , requirements.txt, comprehensve gitignore .
obey SRP. 
make modular scalebale. easy feature add/modify/remove ways 


---

## A. Decisions to lock (defaults proposed — user confirms or overrides)

| # | Decision | Proposed default |
|---|----------|------------------|
| 1 | Sample case ([05](05-sample-case.md)) approved as the build target? | **Yes, build to this exact spec** |
| 2 | Video format | **1920×1080, 16:9, 30 fps, long-form** (YouTube long-form; RPM target in [01](01-product-strategy.md)) |
| 3 | Asset source (LUTs / music / SFX / fonts) | **Auto-source free/royalty-free** (Google Fonts; free `.cube` LUT packs; royalty-free music/SFX dirs) — user can swap later |
| 4 | Claude Opus JSON generation | **Automated via Anthropic API key** (topic → JSON in-pipeline) |
| 5 | Forced aligner | **`stable-ts`** default, **MFA** as precision fallback |
| 6 | Voice | Gemini TTS, **one fixed prebuilt voice** + persona prefix ([06](06-voice-and-timing.md)) |
| 7 | Language / niche | English, dark psychology ([01](01-product-strategy.md)) — locked |

(These are answered via the question prompt accompanying this file; record
final answers in section E.)

## B. Secrets / API keys needed (put in `.env`, NOT in chat)

A `.env.example` is created at project root. User fills a real `.env`:

| Key | Used for | Required |
|-----|----------|----------|
| `GEMINI_API_KEY` | Google Gemini TTS (voiceover) | **Yes** |
| `PEXELS_API_KEY` | Stock video clip search/download | **Yes** |
| `ANTHROPIC_API_KEY` | Claude Opus → script + JSON (if automated, decision #4) | Yes if #4 = automated |
| `YT_CLIENT_ID` | YouTube Data API v3 OAuth client | For auto-upload |
| `YT_CLIENT_SECRET` | YouTube OAuth client secret | For auto-upload |
| `YT_REFRESH_TOKEN` | Long-lived token from one-time consent flow | For auto-upload |

> **YouTube upload is NOT a simple API key.** The Data API v3 `videos.insert`
> acts on behalf of a channel → requires **OAuth 2.0**. User creates an OAuth
> client (Desktop) in Google Cloud Console, runs a one-time local consent
> flow (helper script provided at build) which yields `YT_REFRESH_TOKEN`.
> Quota: an upload costs 1600 units of the default 10,000/day → ~6 uploads/
> day (12/month plan is fine). Until the OAuth app passes Google
> verification, uploads are **forced `private`** (gap G15).

No key for the aligner (stable-ts/MFA run locally) or rendering (MoviePy/
FFmpeg local). If more services are added later, append here and to
`.env.example`.

## C. Pexels handling spec (must be implemented)

The engine **produces its own Pexels search terms and auto-cuts clips** —
no manual clip picking.

1. **Search terms:** Claude emits `clip_query_primary` + `clip_query_backup`
   per segment ([04](04-json-schema.md)). Module queries Pexels **video**
   search; if primary yields nothing usable, tries backup; if still nothing,
   degrades to a generic mood term derived from `beat_type`.
2. **Selection filters:** orientation = landscape; min resolution ≥ 1080p;
   prefer clips whose duration ≥ segment duration; pick highest-res file
   variant ≤ 1080p target.
3. **Auto-cut length:** trim the fetched clip to the segment's
   `end - start` (plus ~150 ms handles for cut transitions). Cut rhythm /
   montage rules come from [03](03-variation-system.md) Layer 5:
   - normal segment → one clip trimmed to segment length;
   - `camera_motion: rapid_clip_montage` → use `montage_clips[]`, each
     `{query, duration}` fetched + trimmed independently and concatenated.
4. **Trim point:** take from the clip's mid-section (skip first/last 0.5 s
   to avoid fade/branding); if clip shorter than needed, slow-mo stretch up
   to 1.25× or loop with a subtle crossfade.
5. **Anti-repetition:** track used Pexels video IDs per video; never reuse
   within the same video; aim for B-roll variety
   (face / texture / environment / object / slow-mo).
6. **Caching:** downloaded clips cached on disk by Pexels ID to avoid
   re-downloading across re-renders.
7. **Attribution:** Pexels license needs no attribution, but log source IDs
   per video to `render_manifest.json` for traceability.

## D. Project layout (created at build start)

```
video_production_2/
  .env / .env.example       # secrets (gitignored)
  config.yaml / config.example.yaml   # per-purpose model config (doc 10)
  BUILD_PROGRESS.md         # live build checklist (read on resume, keep current)
  START_HERE.md             # session entry point
  pyproject.toml / requirements.txt
  src/
    schema/                 # JSON schema + validator
    pipeline/
      script_gen.py         # topic -> script + JSON (Anthropic)
      tts_gemini.py         # scene+delivery prompt -> per-segment audio
      align.py              # stable-ts forced alignment -> word times
      master.py             # loudness/de-ess/comp + silence pacing
      pexels.py             # search-term -> clip -> auto-cut
      render.py             # core engine: 10 variation layers -> MP4
    fx/                     # text anims, camera, color(LUT), sound fx
  assets/ { fonts/ luts/ music/ sfx/ }
  output/ { <video_slug>/ segments/ audio/ clips/ script.md
            final.mp4 thumbnail.jpg metadata.json render_manifest.json }
  planning/                 # these docs
```

## E. Final answers (filled from the question prompt — read this at build)

- [x] #1 Sample case: **doc 05 control signals approved**; script
  origin/segmentation clarified in [09](09-script-pipeline.md) (2-stage
  Claude: write → segment/direct). Build to this.
- [x] #2 Format: **1080p 16:9 long-form** (1920×1080, 30 fps).
- [x] #3 Assets: **auto-source free/royalty-free** into `assets/`.
- [x] #4 Claude JSON automation: **automated** (Anthropic API). Stage 1
  script has a user review gate ([09](09-script-pipeline.md)).
- [x] Metadata (G2): **in scope** — thumbnail + title/desc/tags stage built
  with the pipeline.
- [x] YouTube auto-upload: **in scope** — OAuth, uploads default to
  `private` (see [08](08-risk-gaps.md) G15); user flips to public/schedules.
- [ ] Keys present in `.env`: GEMINI / PEXELS / ANTHROPIC / YouTube OAuth
  (`YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`)

## F. Build trigger

User says "build" in bypass mode →
0. Read [BUILD_PROGRESS.md](../BUILD_PROGRESS.md) — resume at first unchecked
   item; never rebuild a `[x]`.
1. Read sections A–E here.
2. Scaffold layout (D), write `requirements.txt`, validate `.env` +
   `config.yaml` (per-purpose models, [10](10-model-config.md)).
3. Build in the order from [02](02-system-architecture.md#suggested-build-order);
   **after each component is built & smoke-tested, update
   [BUILD_PROGRESS.md](../BUILD_PROGRESS.md)** before continuing.
4. End-to-end test on the sample case ([05](05-sample-case.md)) →
   `output/<slug>/` (local save is the deliverable).
5. Attempt YouTube upload as a **non-blocking** final step: local copy is
   always kept; upload failure logs to `render_manifest.json` and the run
   still exits success.
