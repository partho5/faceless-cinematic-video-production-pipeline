# START HERE — entry point for a fresh session

> Open **this file first** in any new session. It says what this project is
> and exactly how to begin the build.

## What this is

A Python pipeline that turns a topic into a finished, cinematic
dark-psychology YouTube video with **zero manual editing**:

`topic → Claude script → Claude control-JSON → Gemini TTS → forced-align →
render (10 variation layers) → thumbnail + metadata → save local → upload`

## To build (user will say this in permission-bypass mode)

1. **Read [BUILD_PROGRESS.md](BUILD_PROGRESS.md) FIRST.** It is the live
   record of which components are already built & tested. Resume from the
   first unchecked item — never rebuild a `[x]` item.
2. Read [planning/07-build-readiness.md](planning/07-build-readiness.md) —
   every locked decision (A/E), keys, Pexels spec, layout, build trigger.
   **Source of truth for the build.**
3. Models are config-driven: read `config.yaml`
   ([template](config.example.yaml), spec
   [planning/10-model-config.md](planning/10-model-config.md)). No hardcoded
   model IDs — each purpose (script / segmentation / metadata / thumbnail /
   tts / alignment) gets its model from config.
4. Build in the order in
   [planning/02-system-architecture.md](planning/02-system-architecture.md#suggested-build-order).
   **After each component is built and smoke-tested, update
   [BUILD_PROGRESS.md](BUILD_PROGRESS.md)** (flip the box, add date + note,
   add a Log line) before moving on.
5. End-to-end test against
   [planning/05-sample-case.md](planning/05-sample-case.md) →
   writes to `output/<slug>/` (local save is the deliverable; YouTube
   upload is best-effort and non-blocking).

## Read order for full context

| Order | Doc | Why |
|-------|-----|-----|
| 1 | [planning/07-build-readiness.md](planning/07-build-readiness.md) | Locked decisions, keys, layout, trigger — **start here for build** |
| 2 | [planning/02-system-architecture.md](planning/02-system-architecture.md) | Pipeline, stack, components, build order |
| 3 | [planning/09-script-pipeline.md](planning/09-script-pipeline.md) | Where the script comes from (2-stage Claude) |
| 4 | [planning/04-json-schema.md](planning/04-json-schema.md) | The JSON contract |
| 5 | [planning/06-voice-and-timing.md](planning/06-voice-and-timing.md) | Gemini TTS + forced-alignment word timing |
| 6 | [planning/03-variation-system.md](planning/03-variation-system.md) | The 10 visual/audio variation layers |
| 7 | [planning/05-sample-case.md](planning/05-sample-case.md) | Worked example = the e2e test target |
| 8 | [planning/08-risk-gaps.md](planning/08-risk-gaps.md) | Known gaps + their fixes |
| 9 | [planning/01-product-strategy.md](planning/01-product-strategy.md) | Why this niche/format (business context) |
| 10 | [planning/10-model-config.md](planning/10-model-config.md) | Per-purpose model config (config.yaml) |
| — | [BUILD_PROGRESS.md](BUILD_PROGRESS.md) | Live build checklist — read on resume, update after each component |
| — | [planning/README.md](planning/README.md) | Index of all planning docs |

## Before triggering build

- Fill `.env` from [.env.example](.env.example): `GEMINI_API_KEY`,
  `PEXELS_API_KEY`, `ANTHROPIC_API_KEY`, and (optional, non-blocking)
  `YT_CLIENT_ID` / `YT_CLIENT_SECRET` / `YT_REFRESH_TOKEN`.
- Copy [config.example.yaml](config.example.yaml) → `config.yaml` (or accept
  defaults) to choose models per purpose.
- Missing YouTube creds is fine — the video still renders and saves to
  `output/`; only the upload step is skipped.

## Status

Planning complete and approved (1080p 16:9 long-form; auto-sourced assets;
automated Claude JSON with a script review gate; metadata + non-blocking
YouTube upload in scope). **Next action: user triggers the build.**
