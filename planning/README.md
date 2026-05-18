# Project: Automated Dark-Psychology Video Production Engine

Faceless YouTube channel system. A topic goes in, Claude Opus emits a rich
per-segment JSON "direction sheet", and a Python engine renders a finished,
cinematic MP4 with **zero manual editing**.

The core bet: most faceless channels look identical and the algorithm
suppresses them. This system wins on **emotionally-driven visual/audio
variation** across 10 dimensions, programmatically applied.

## Planning Documents

| File | Contents |
|------|----------|
| [01-product-strategy.md](01-product-strategy.md) | Niche choice, audience, revenue math, targets, timeline |
| [02-system-architecture.md](02-system-architecture.md) | Pipeline, tech stack, components to build in Claude Code |
| [03-variation-system.md](03-variation-system.md) | The 10 variation layers (the actual differentiator) |
| [04-json-schema.md](04-json-schema.md) | Video-level + segment-level JSON contract |
| [05-sample-case.md](05-sample-case.md) | Worked example: title, script, full JSON, segment JSON |
| [06-voice-and-timing.md](06-voice-and-timing.md) | Google Gemini TTS quality strategy + word-timing via forced alignment |
| [07-build-readiness.md](07-build-readiness.md) | Locked decisions, API keys, Pexels spec, project layout, build trigger |
| [08-risk-gaps.md](08-risk-gaps.md) | Other technical gaps/risks to handle before/while building |
| [09-script-pipeline.md](09-script-pipeline.md) | Where the script comes from: 2-stage Claude (write → segment/direct) |
| [10-model-config.md](10-model-config.md) | Per-purpose model config (config.yaml): script/segmentation/tts/thumbnail/alignment |

## New session?

Open [../START_HERE.md](../START_HERE.md) first — it is the single entry
point and points to the build trigger.

## Status

Awaiting user confirmation of the sample case (see doc 05) before the full
build begins. Once confirmed: build schema validators + Python rendering
engine first.
