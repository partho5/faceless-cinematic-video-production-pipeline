# BUILD PROGRESS — single source of truth for "what is built"

> The build session MUST keep this file current. Update it **after each
> component is built AND smoke-tested** — flip `[ ]`→`[x]`, add the date and
> a one-line note. On resume, read this FIRST to know where to continue.
> Never re-build a `[x]` component; never mark `[x]` before its test passes.

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done & tested

## Components (order from planning/02 build order)

- [x] 0. Scaffold: project layout, requirements.txt, load .env + config.yaml
- [x] 1. JSON schema + validator (planning/04)
- [x] 2a. Gemini TTS module (scene+delivery prompt, per-segment audio)
- [x] 2b. Forced-alignment module (stable-ts → word times) (planning/06)
- [x] 3. Render engine skeleton (ingest JSON → MP4 with synced audio)
- [x] 4. Pexels fetch + auto-cut + anti-repetition (planning/07 §C)
- [x] 5. Text rendering driven by aligned word times (variation Layer 2)
- [x] 6. Camera motion + cut rhythm (Layers 3, 5)
- [x] 7. LUT color grading + always-on FX: grain/vignette (Layers 4, 9)
- [x] 8. Sound FX + music ducking + mastering (Layer 6, gap G10)
- [x] 9. Script pipeline: Stage 1 writer + review gate (planning/09)
- [x] 10. Stage 2 segmenter/director, per-chapter + validate/repair (G3)
- [x] 11. Metadata stage: thumbnail + title/desc/tags (gap G2)
- [x] 12. YouTube upload module — non-blocking, local-save-first (G15)
- [x] 13. End-to-end run on sample case (planning/05) → output/<slug>/
- [x] 14. Automated QA checks + render_manifest.json (gap G13)

## Log (newest first)

<!-- e.g. 2026-05-18  [x] 1 schema+validator: validates sample 05 JSON, 12 tests green -->
- 2026-05-17  [x] 14 qa+manifest: duration/black/silence/alignment QA gate + render_manifest.json (G14 models, G11 provenance/disclosure, runtime actual-mode, cost). QA passed.
- 2026-05-17  [x] 13 e2e: `python -m vp.run "<topic>" --approve` -> output/<slug>/ {script.md,video.json,master.wav,final.mp4,thumbnail.jpg,metadata.json,render_manifest.json}. 9-seg sample, all QA OK. NOTE: ran on offline stubs (client libs not installed); .env now has real keys — see msg.
- 2026-05-17  [x] 12 youtube: lazy OAuth videos.insert, forced-private (G15), synthetic-media flag, never raises/deletes; scripts/youtube_oauth_setup.py helper. Smoke: skipped path.
- 2026-05-17  [x] 11 metadata: best-frame 1280x720 thumbnail + Pillow hook text; title variants/desc/tags (lazy Anthropic + offline), chapter timestamps, G11 disclosure. Smoke green.
- 2026-05-17  [x] 10 stage2: per-chapter lazy Anthropic (cached director sys) + validate-and-repair loop (G3), unique ids, video.json; offline sample-05 doc. Smoke: 9 segs validate ok.
- 2026-05-17  [x] 9 stage1: topic->script.md via lazy Anthropic (cached system) + offline sample-05 fallback; review_gate (auto / APPROVED sentinel). Smoke green.
- 2026-05-17  [x] 8 master: HPF+comp+limiter voice, synth/asset music ducked by voice envelope, JSON-timed SFX (asset or procedural), 2-pass loudnorm ~-14 LUFS. Smoke: 7 SFX, -15 LUFS.
- 2026-05-17  [x] 7 color/fx: real .cube loader (optional) + 13 synthetic grades, grain/vignette/chromatic per-segment intensity. Verified threat grade reddens frame.
- 2026-05-17  [x] 6 camera+cuts: push/pull/crash/drift/shake/dutch/spin/whip via cv2 affine; head/tail cut transitions (dip/flash/dissolve). Verified dip_from_black -> black frame0.
- 2026-05-17  [x] 5 text: Layer2/8 font resolver (system fallback), word-timed reveal/typewriter/slam, emphasis (scale/glow/shake/dim_rest), exit anims, personality styling. Smoke green.
- 2026-05-17  [x] 4 pexels: lazy real search+cache+filters+midcut, generic beat fallback, anti-repetition, montage, offline procedural bank, provenance manifest. Smoke green (montage+anti-rep verified).
- 2026-05-17  [x] 3 render skeleton: G1 timeline reflow + pluggable visual source + frame-xform chain + voice mux; preview/final presets. Smoke: 3-seg MP4, dur matches audio.
- 2026-05-17  [x] 2b align: lazy stable-ts + proportional fallback (offline/low-conf/err, G9), digit/punct normalization, multi-word emphasis span lookup. Smoke green.
- 2026-05-17  [x] 2a tts: persona+prevline+scene+delivery prompt, multi-take auto-select, lazy google-genai, offline synth fallback (real 24k WAV). Smoke green.
- 2026-05-17  [x] 1 schema+validator: typed model + repair/reject validator; sample-05 loader (derives tts_scene/delivery); 6 tests green.
- 2026-05-17  [x] 0 scaffold: src/ layout, venv, requirements, .gitignore, config.py (per-purpose model resolve + .env + offline detection). Smoke test green.
