# Video Production Pipeline

**Type a topic. Get back a finished, cinematic, ready-to-upload video. No editing.**

---

## What it produces (the short, non-technical version)

You give it a single line — *"The 7 signs someone is quietly manipulating you"*,
or *"A little star's bedtime"* — and it returns a complete **1080p video** with:

- a **written, narrated story** in a consistent, natural-sounding voice,
- **word-perfect on-screen captions** that light up exactly as each word is spoken,
- **cinematic motion and grading** — slow pushes, pans, mood color, film grain,
  light leaks — so it looks directed, not auto-generated,
- a **music bed and sound effects** mixed and loudness-normalized for YouTube,
- a **thumbnail, title, description and tags**,
- an automatic **quality check**, and an optional **forced-private upload to YouTube**.

The result feels like a faceless YouTube channel video that took a human editor a
day — produced end-to-end in one command, with **zero manual editing**. The default
voice and look are tuned for the **dark-psychology / cinematic-narration** niche,
but the scene and tone are steerable, so it equally handles calmer content like
sleep stories or stoic-discipline shorts.

**The value:** it collapses scriptwriting, voiceover, captioning, editing,
grading, sound design, thumbnail/metadata and QA into one reproducible pipeline.

---

## What you actually get from a run

```
output/<slug>/
├── final.mp4            # the deliverable — 1920×1080 @ 30fps, mixed audio
├── thumbnail.jpg        # generated thumbnail
├── metadata.json        # title variants, description, tags, chapters
├── master.wav           # final mixed audio (voice + music + SFX, -14 LUFS)
├── script.md            # the narration script
├── video.json           # the full control document (every per-segment decision)
└── render_manifest.json # models used, QA results, provenance, runtime
```

---

## How it works (technical)

A topic flows through a staged pipeline; every creative decision is captured in a
single **control JSON** so the render is fully reproducible:

```
topic
  → Stage 1: Claude writes the narration script
  → review gate (optional human approve)
  → Stage 2: Claude segments it + emits per-segment control signals (JSON)
  → Gemini TTS: continuous per-chapter narration (one fixed channel voice)
  → forced alignment (stable-ts): word-level timestamps
  → lossless slicing: chapter audio → per-segment clips, cut only at word onsets
  → timeline reflow: measured audio is the source of truth (no drift)
  → render: 10 cinematic layers composited per frame
  → master mix: voice + ducked music + JSON-timed SFX, 2-pass loudnorm
  → metadata + thumbnail
  → QA gate + render_manifest
  → output/<slug>/  (the local file IS the deliverable)
  → optional non-blocking, forced-private YouTube upload
```

### The 10 cinematic layers (per-segment, recombined every few seconds)

1. **Emotional beat mapping** — each beat (hook, tension, reveal, shock…) drives a recipe
2. **Text animation library** — word-by-word reveals, slams, typewriter, emphasis, exits
3. **Camera motion** — push/pull, drift, shake, dutch, whip-pan, **always-on Ken Burns**
4. **Color grading** — 13 mood grades (cold isolation, threat, dream, memory…)
5. **Cut rhythm** — speech-driven, not fixed-interval; dips, flash frames, dissolves
6. **Sound design** — voice processing, music bed, JSON-triggered SFX
7. **Clip selection** — Pexels stock + procedural fallback, no clip reused in a video
8. **Typography personality** — aggressive / clinical / whisper / reveal / handwritten
9. **Ambient FX** — grain, vignette, chromatic aberration, light leaks, dust, scan lines
10. **Whole-video arc** — opening dense, body calmer, climax peaks, outro slows

### Key engineering properties

- **Config-driven models** — `config.yaml` names which model serves which purpose
  (script / segmentation / metadata / TTS / alignment); swap models without code changes.
- **Audio-as-truth timeline** — visuals are reflowed onto measured narration, so
  captions never drift from speech.
- **Resumable TTS cache** — each chapter's audio is hashed (text + voice + model +
  scene + context); re-runs reuse it with **zero API calls / zero quota spend**.
- **API-key rotation** — multiple Gemini keys rotate with 429 cooldown parking to
  survive free-tier limits on 10–12 min videos.
- **Offline fallbacks** — no keys ⇒ local Piper voice + proportional alignment +
  procedural visuals, so it still produces a valid video.
- **Locale-independent I/O** — all text I/O is explicit UTF-8.

> Caption rendering and forced alignment are currently tuned for Latin/Greek/
> Cyrillic scripts and English audio. CJK/Arabic/Indic captions and non-English
> alignment are a known limitation, not yet implemented.

---

## Tech stack

Python ≥ 3.10 · NumPy · Pillow · OpenCV · MoviePy / FFmpeg · pydub ·
Anthropic Claude (script + control JSON + metadata) · Google Gemini (TTS) ·
stable-ts (forced alignment) · Pexels API (stock footage) ·
YouTube Data API v3 (optional upload) · pytest.

---

## Install

Requires **Python ≥ 3.10** and **FFmpeg** on the system.

```bash
git clone <your-repo-url>
cd video-production-pipeline

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt        # full stack
# minimal offline render only:
# pip install numpy Pillow PyYAML requests moviepy pydub opencv-python-headless
```

`stable-ts` pulls in torch/whisper and is heavy; it is lazy-imported, so the
pipeline still runs (with proportional alignment) if you skip it.

The offline voice fallback uses a Piper `.onnx` model in `assets/tts_voices/`
(git-ignored due to size — download separately if you want the offline voice;
otherwise online Gemini TTS is used).

---

## Configure

```bash
cp .env.example .env                 # fill in your keys (gitignored — never commit)
cp config.example.yaml config.yaml   # choose models per purpose (gitignored)
```

**`.env`** holds secrets only:

| Key | Purpose | Required? |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude — script, control JSON, metadata | for live script gen |
| `GEMINI_API_KEY` (and `GEMINI_API_KEY_1/2/3…`) | Gemini TTS; extra keys rotate for free-tier survival | for real voice |
| `PEXELS_API_KEY` | Stock footage search/download | optional (procedural fallback) |
| `YT_CLIENT_ID` / `YT_CLIENT_SECRET` / `YT_REFRESH_TOKEN` | YouTube auto-upload (OAuth) | optional |

**`config.yaml`** maps purpose → provider/model/params. Defaults: Claude Opus for
script & segmentation, Claude Sonnet for metadata, Gemini for TTS (one fixed
channel voice), local stable-ts for alignment. No secrets live here.

With **no keys at all**, the pipeline runs fully offline (local voice,
proportional alignment, procedural visuals) and still emits a valid `final.mp4`.

---

## Quickstart

End-to-end from a topic (preview = fast 960×540, final = 1920×1080):

```bash
# fast smoke render, auto-approve the script gate:
python -m vp.run "The 7 signs someone is quietly manipulating you" --approve

# production render:
python -m vp.run "The 7 signs someone is quietly manipulating you" \
    --approve --preset final --no-upload
```

Flags: `--approve` skips the human review gate · `--preset preview|final` ·
`--segments N` caps segments (quick tests) · `--no-upload` keeps it local-only.

### Parameterized example scripts

Edit the `CONSTANTS` block (title / niche / TTS scene+context / script) and run:

```bash
python scripts/make_video.py          # short production video, per-niche steering
python scripts/make_short_video.py    # tiny 4–5 line script, full 1080p path
```

Long 1080p renders can exceed a short shell timeout — run them in the background.

### YouTube upload (optional, one-time setup)

```bash
python scripts/youtube_oauth_setup.py   # OAuth consent → prints YT_REFRESH_TOKEN
```

Uploads are **non-blocking** and **forced private** (an unverified OAuth app
cannot publish publicly anyway). The local `final.mp4` is always the deliverable.

---

## Testing

```bash
pytest -q          # schema, lossless slicing regression, TTS cache, ambient fx
```

---

## Project layout

```
src/vp/
  run.py            # end-to-end orchestrator + CLI
  config.py         # config.yaml + .env loader, model resolution, offline detect
  pipeline/         # script_gen, voice (TTS+cache+rotation), align, timeline,
                    #   render, master, metadata, qa, pexels, youtube
  fx/               # text, camera, color, ambient (Ken Burns / leaks / arc), fonts
  schema/           # control-document model, validator, enums
scripts/            # make_video, make_short_video, finish_from_artifacts, yt oauth
tests/              # pytest suite
planning/           # design docs (01–10): strategy, architecture, variation system…
```

---

## Notes & constraints

- Secrets stay in `.env` (gitignored) — never committed; values never printed.
- YouTube uploads are forced-private and non-blocking by design.
- Multilingual captions/alignment (CJK/Arabic/Indic, non-English) are not yet
  implemented; everything else degrades gracefully to offline fallbacks.
