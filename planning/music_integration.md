# Music Integration — Complete Planning Document

**Status as of 2026-05-19:** Phase 1 fully implemented and tested. Five Phase 2 gaps remain (documented at the end).

This document is self-contained. A new session starting only from this file has all context needed to understand what was built, why, and where to continue.

---

## 1. Philosophy

Three rules, in priority order:

1. **No music is better than wrong music.** Wrong music actively damages a video. Silence damages nothing.
2. **No music is better than hallucinated music.** If the LLM picks a track name that doesn't exist in the catalog or isn't on disk → silence. Never invent a fallback sound.
3. **Separation of LLM decisions is non-negotiable.** One decision type = one dedicated API call = one purpose key in config = one class = one offline fallback. Mixing decisions in a single call is forbidden, even if it would save tokens.

These rules come from the project owner explicitly. Do not compromise them for convenience.

---

## 2. LLM Decision Taxonomy

The pipeline has three tiers of editorial decisions. Each tier is its own API call:

| Tier | What it decides | When | Class |
|------|----------------|------|-------|
| Script-level creative | Narration, story structure | Stage 1 + Stage 2 | `ScriptStage`, `SegmentStage` |
| **Macro-editorial** | **Which background music track and presence tier** | **After Stage 2 validation** | **`MusicDesigner`** |
| Micro-editorial | Which SFX cues, at what word timings | After alignment | `SoundDesigner` |

Music is **macro-editorial**: one global decision per video based on niche and emotional tone. It runs before TTS so any failure is discovered before burning voice API credits. SFX is **micro-editorial**: per-moment placement on an already-known timeline. These two must never share an API call.

---

## 3. Pipeline Position

```
Stage 1: ScriptStage (script.md)
         ↓ review gate
Stage 2: SegmentStage (video.json) → validate()
         ↓
Stage 2b: MusicDesigner (music.json) ← YOU ARE HERE (Phase 1 complete)
         ↓
Stage 3: VoiceStage → align_segment → reflow
         ↓
Stage 5b: SoundDesigner (SFX cues)
         ↓
Stage 6: build_master (master.wav) — mixes voice + music + SFX
         ↓
Stage 7: RenderEngine (final.mp4)
         ↓
Stage 8: MetadataStage + thumbnail
         ↓
Stage 9: QA + manifest
         ↓
Stage 10: YouTube upload
```

**Why before TTS?** A music failure is cheap to discover and act on. Failing after TTS wastes voice API credits.

---

## 4. Files Changed / Created

### 4a. `assets/music/catalog.json` (managed artifact)

Schema v1.3. Every track has these fields:

```json
{
  "file": "slug-name.mp3",
  "title": "Human Title",
  "artist": "Artist Name",
  "duration_sec": 267,
  "license": "CC BY 4.0",
  "attribution": "Backbay Lounge by Kevin MacLeod – incompetech.com (CC BY 4.0)",
  "source_url": "https://archive.org/...",
  "mood_buckets": ["warm_cozy", "lofi_focus"],
  "energy": "low",
  "niches": ["cooking", "food", "lifestyle"],
  "tags": ["jazz", "lounge", "warm"],
  "loop_start_s": 6.5,
  "loop_end_s": 261.0
}
```

**Key fields:**
- `file` — slug used by LLM. Filename without path. Always ends in `.mp3`.
- `mood_buckets` — closed vocabulary from the 15 buckets defined in the catalog header.
- `loop_start_s` / `loop_end_s` — the usable window. Pipeline slices audio to this range before looping. Set by `scripts/analyze_music_loops.py`, not by hand.
- `license` — determines attribution obligation. `CC BY 4.0` tracks (Kevin MacLeod) **require** credit in video description.
- `attribution` — exact credit string to use. Null for public domain / generic royalty-free.

**19 tracks currently on disk** (all gitignored; catalog.json IS tracked):
```
broken-lantern-lofi-chill.mp3         → lofi_focus
celebration-upbeat.mp3                → upbeat_pop, energetic_hype
clouds-huma-huma.mp3                  → calm_nature_ambient, minimal_near_silent
eclipse-flux-synthwave.mp3            → electronic_future
fresh-fallen-snow.mp3                 → calm_nature_ambient, warm_cozy
kai-hartwig-angry-sea.mp3             → suspenseful_tense, dramatic_serious
kai-hartwig-city-of-gold.mp3          → adventure_cinematic, inspiring_motivational
kai-hartwig-dungeon-runners.mp3       → energetic_hype, electronic_future
kai-hartwig-heart-of-courage.mp3      → inspiring_motivational, epic_orchestral
kai-hartwig-march-goblin.mp3          → eerie_dark, dramatic_serious
kai-hartwig-pirate-chase.mp3          → adventure_cinematic, energetic_hype
kevin-macleod-backbay-lounge.mp3      → warm_cozy, lofi_focus
kevin-macleod-bossa-antigua.mp3       → warm_cozy, lofi_focus
kevin-macleod-dance-of-deception.mp3  → suspenseful_tense, eerie_dark
kevin-macleod-district-four.mp3       → corporate_clean, dramatic_serious
kevin-macleod-evening-of-chaos.mp3    → eerie_dark, dramatic_serious
kevin-macleod-funky-chunk.mp3         → playful_quirky, warm_cozy
kevin-macleod-peace-of-mind.mp3       → minimal_near_silent, calm_nature_ambient
sunny-thoughts-corporate.mp3          → corporate_clean, inspiring_motivational, upbeat_pop
```

**15 mood buckets** (defined in catalog.json `mood_buckets` dict):
`lofi_focus`, `corporate_clean`, `upbeat_pop`, `playful_quirky`, `energetic_hype`,
`electronic_future`, `adventure_cinematic`, `inspiring_motivational`, `epic_orchestral`,
`suspenseful_tense`, `eerie_dark`, `calm_nature_ambient`, `dramatic_serious`, `warm_cozy`,
`minimal_near_silent`

**To add a new track:**
1. Drop the `.mp3` into `assets/music/`
2. Add an entry to `catalog.json` (leave `loop_start_s`/`loop_end_s` at 0.0 / duration_sec)
3. Run `python scripts/analyze_music_loops.py` to detect and write the loop window

### 4b. `config.yaml` and `config.example.yaml`

Added after `sound_design`:
```yaml
music_design:
  provider: anthropic
  model: claude-haiku-4-5-20251001
  api_key_env: ANTHROPIC_API_KEY
  params: { max_tokens: 200, temperature: 0.2 }
```

Haiku is appropriate here: the task is closed-vocabulary selection (pick one slug from a list), not creative generation. Low temperature (0.2) for deterministic selection.

**`music_design` is NOT in `REQUIRED_PURPOSES`** (`src/vp/config.py`). A missing or misconfigured key → the stage fails gracefully to silence, never crashes the pipeline. This matches the philosophy: no music > pipeline failure.

`music_design` IS in `_LLM_OVERRIDE_PURPOSES` in `config.py`, so `VP_LLM_MODEL=music_design:...` env override works.

### 4c. `src/vp/pipeline/music_design.py` (new file)

Full path: `src/vp/pipeline/music_design.py`

**Purpose:** One LLM pass — picks a single track slug and a presence tier. Writes nothing. Caller checkpoints to `out/music.json`.

**Constants:**
```python
_PRESENCE_VOLUME = {"subtle": 0.10, "moderate": 0.18, "prominent": 0.26}
_FADE_IN_S  = 2.0   # fixed, after hook silence ends
_FADE_OUT_S = 2.5   # fixed
```

**LLM output contract** (strict JSON, nothing else):
```json
{"track": "<slug or null>", "presence": "subtle|moderate|prominent", "reason": "<one line>"}
```

**Validation rules in `design()`:**
1. `slug not in catalog_slugs()` → None (unknown slug)
2. `(ASSETS / "music" / f"{slug}.mp3").exists()` is False → None (file absent)
3. `pres not in _PRESENCE_VOLUME` → "moderate" (fallback to default tier)
4. `slug is None` → `volume = 0.0`, all meta fields set to None

**`design()` return dict** (always complete, safe to checkpoint):
```python
{
    "meta_patch": {
        "background_music_track": slug,        # None if no pick
        "music_master_volume": volume,         # 0.0 if no pick
        "music_fade_in_s": 2.0,
        "music_fade_out_s": 2.5,
        "music_loop_start_s": 10.5,            # from catalog, None if no pick
        "music_loop_end_s": 156.8,             # from catalog, None if no pick
    },
    "track": slug,
    "presence": pres,
    "reason": reason,
    "offline": bool,
    "model": str,
}
```

**Offline behavior:** `self.spec.offline` → returns `{}` from `_call_llm`, which produces slug=None → silence. Never raises.

**Prompt structure:**
- System: palette block (all tracks as `slug | mood_buckets | niches | energy`), presence tier definitions, rules
- User: `VIDEO TITLE: <title>\n\nOPENING NARRATION:\n<first 150 words of segment text_overlay>`
- Note: passing opening narration is a limitation — see Phase 2 gap #2

### 4d. `src/vp/pipeline/master.py` (significantly modified)

**New constants:**
```python
_MUSIC_HOOK_MIN_S   = 3.0
_MUSIC_HOOK_MAX_S   = 6.0
_MUSIC_LOOP_XFADE_S = 3.0
```

**New function: `_decode_mp3(path: Path) -> np.ndarray`**

Decodes MP3 to mono float32 at pipeline SR (24000 Hz) via ffmpeg pipe. Returns `_EMPTY` (zeros shape 0) on any failure.

```python
result = subprocess.run(
    ["ffmpeg", "-i", str(path), "-f", "f32le", "-ar", str(SR), "-ac", "1", "-"],
    capture_output=True,
)
```

**CRITICAL:** SR must be 24000, not 44100. The pipeline uses `from ..audio_util import SR` which is 24000.

**New function: `_tile_with_crossfade(raw, n_total, xfade_n)`**

Tiles `raw` to fill `n_total` samples. Each copy overlaps the previous by `xfade_n` samples with reciprocal linear fades:
- `step = track_len - xfade_n` (advance per copy)
- Outgoing copy tail × `linspace(1→0, xfade_n)`, incoming copy head × `linspace(0→1, xfade_n)`, summed
- Result: zero hard discontinuities at loop boundaries

Without this, `np.tile()` creates a hard seam at every loop point (audible click or pop).

**Rewritten function: `_music_bed(total_s, meta, hook_s)`**

1. Read `meta["background_music_track"]` → None → return zeros
2. Check file exists → missing → return zeros
3. `_decode_mp3(path)` → empty → return zeros
4. Slice to loop window: `raw = raw[int(loop_start_s*SR) : int(loop_end_s*SR)]`
5. `_tile_with_crossfade(raw, n_total, int(3.0*SR))`
6. **Hook silence:** `bed[:int(hook_s*SR)] = 0.0` — completely silent for hook duration
7. **Fade in:** linear ramp from hook_end to hook_end + fade_in_n
8. **Fade out:** linear ramp over last fade_out_n samples

**In `build_master()`:**
```python
first_seg_dur = segments[0].duration if segments else 0.0
hook_s = float(np.clip(first_seg_dur, _MUSIC_HOOK_MIN_S, _MUSIC_HOOK_MAX_S))
music = _music_bed(total_s, meta, hook_s)[: len(voice)]
```

Manifest now includes `"music_hook_silence_s": round(hook_s, 2)`.

### 4e. `src/vp/run.py` (modified)

Music design stage inserted between Stage 2 validation and Stage 3 (TTS/Voice).

**Resume checkpoint pattern** (mirrors Stage 2's `video.json`):
```python
from .pipeline.music_design import MusicDesigner

_music_json = out / "music.json"
md: dict | None = None
if resume and _music_json.exists():
    try:
        cached = json.loads(_music_json.read_text(encoding="utf-8"))
        doc.video_meta.update(cached["meta_patch"])
        md = cached
        _log(f"resume: music_design skipped (track={cached.get('track') or 'none'})")
    except Exception:
        md = None
if md is None:
    md = MusicDesigner(cfg).design(doc)
    _music_json.write_text(json.dumps(md, ensure_ascii=False, indent=2), encoding="utf-8")
    doc.video_meta.update(md["meta_patch"])
```

Added to `runtime` dict (written to `render_manifest.json`):
```python
"music_design": {
    "track": md.get("track"),
    "presence": md.get("presence"),
    "reason": md.get("reason"),
    "offline": md.get("offline"),
    "model": md.get("model"),
}
```

### 4f. `scripts/analyze_music_loops.py` (new file)

**Purpose:** One-time analysis script. Detects the usable loop window for every track and writes `loop_start_s` / `loop_end_s` back to `catalog.json`. Run whenever tracks are added.

**Usage:**
```bash
python scripts/analyze_music_loops.py            # analyze + update catalog
python scripts/analyze_music_loops.py --dry-run  # print only, no write
```

**Algorithm (numpy-only, no librosa):**
1. Decode MP3 via ffmpeg pipe at SR=24000
2. Short-time RMS envelope (0.5s window, 0.1s hop)
3. Smooth with 1s causal moving-average
4. `loop_start_s`: first frame ≥ 55% of peak RMS where next 3s all stay ≥ 40% of peak
5. `loop_end_s`: last frame ≥ 55% of peak + 0.5s window + 1.0s outro margin
6. Sanity: `if loop_start_s > total_s * 0.25 → (0.0, total_s * 0.95)` — prevents late energy spikes from becoming false "peak"
7. Minimum loop window: 10s; if shorter, expand to 80% of track

**ASCII bar output:** Unicode block chars (▁▂▃▄▅▆▇█) with `^` markers at detected start/end. Lets you spot-check results before writing.

**When to re-run:** Any time a new track is added to the catalog. Results are written in-place to `catalog.json`.

---

## 5. Hook Protection Design

**The rule:** The opening hook (first segment) is the most important part of any short-form video. Music must not compete with it.

**Implementation:**
- Music is **completely silent** (amplitude = 0.0) for `clamp(segments[0].duration, 3.0, 6.0)` seconds from the start
- After hook silence ends, music **fades in linearly** over `music_fade_in_s = 2.0s`
- This is **code policy, not LLM**. The LLM is not consulted about hook duration.

**Where it lives:** `master.py:build_master()` — `hook_s` computed from `segments[0].duration`, clamped to [3, 6], passed to `_music_bed()`.

---

## 6. Loop/Tiling Design

**Problem:** A 120s track for a 6-minute video must loop 3× without audible seams.

**Old behavior:** `np.tile(raw, repeats)` — hard discontinuity at each loop boundary. Sounds like a click or a sudden restart.

**Current behavior:** `_tile_with_crossfade(raw, n_total, xfade_n=3s)`:
- Each copy of the track overlaps the previous by 3 seconds
- Outgoing copy fades from 1→0 over the overlap; incoming copy fades from 0→1
- They are summed → smooth transition, no discontinuity

**Loop window:** Not the whole track. `loop_start_s` skips a slow intro; `loop_end_s` stops before an outro fade. The LLM (in real video editing) selects a mm:ss→mm:ss window. Here, `analyze_music_loops.py` detects this window automatically from the RMS envelope.

---

## 7. Beat Types (from real Stage 2 output)

Stage 2 (`SegmentStage`) produces these beat types in segment metadata. Relevant for Phase 2 gap #2 and gap #4:

```
hook_opener          — aggressive opening, stakes established
hook_closer_reveal_tease — end of hook section, creates curiosity gap
tension_build        — escalating conflict or stakes
revelation_setup     — about to reveal something significant (often needs silence)
stakes_escalation    — increasing consequence
contrast_drop        — tonal shift, often quieter or more intimate
```

These are the real values seen in production output. More may exist; these are confirmed.

---

## 8. Attribution / Legal Obligation

**CC BY 4.0 tracks (Kevin MacLeod):** The license legally requires attribution. The `attribution` field in the catalog contains the exact credit string. Example:
```
Backbay Lounge by Kevin MacLeod – incompetech.com (CC BY 4.0)
```

This string must appear in the YouTube video description when the track is used. Currently this is **not yet implemented** in the pipeline (see Phase 2 gap #5).

Tracks with `attribution: null` (generic royalty-free) require no credit.

---

## 9. Phase 2 Gaps (not yet implemented)

These five gaps were designed and discussed but not coded. Implement in any order; each is independent.

---

### Gap 1: Confidence gate from LLM

**Motivation:** If the LLM is uncertain, its guess is worse than silence.

**What to change:**

In `music_design.py` — add `"confidence"` to the LLM output contract in `_system_prompt()`:
```
OUTPUT — strict JSON, nothing else:
{"track": "<slug or null>", "presence": "...", "confidence": "low|high", "reason": "..."}
```

In `design()` — add after slug validation:
```python
if raw.get("confidence") == "low":
    _log(f"music design: LLM low confidence on {slug!r} -> no music")
    slug = None
```

**Size:** Prompt change + 3 lines of code.

---

### Gap 2: Beat-type context instead of 150 words of narration

**Motivation:** Passing opening narration is misleading. A video with a dramatic cold open may have narration that sounds tense, but the overall niche is "self-help" — the music should be motivational, not ominous. The opening text does not represent the full video.

**Better input:** Beat-type distribution (the emotional arc of the whole video).

**What to change:**

In `music_design.py` — replace `_user_prompt()`:
```python
def _user_prompt(doc: ControlDocument) -> str:
    title = str(doc.video_meta.get("title", "") or "")
    # beat-type arc: aggregated from all segments
    beat_counts: dict[str, int] = {}
    for s in doc.segments:
        bt = getattr(s, "beat_type", None) or "unknown"
        beat_counts[bt] = beat_counts.get(bt, 0) + 1
    arc = ", ".join(f"{bt}({n})" for bt, n in beat_counts.items())
    return f"VIDEO TITLE: {title}\n\nBEAT-TYPE ARC:\n{arc}"
```

Check that `Segment` has a `beat_type` field in `src/vp/schema/model.py` first. If absent, check what Stage 2 actually calls it.

**Why this is better:** The beat-type distribution captures the whole video's emotional structure, not just the cold open.

---

### Gap 3: Niche cross-validation (LLM-independent guard)

**Motivation:** LLM can be confident and wrong. The catalog `niches` field is a code-level sanity check that runs regardless of LLM confidence.

**What to change:**

In `music_design.py` — add after slug validation, before volume assignment:
```python
if slug is not None:
    video_niche = str(doc.video_meta.get("niche", "") or "").lower()
    track_niches = set()
    for t in tracks:
        if t.get("file", "").removesuffix(".mp3") == slug:
            track_niches = {n.lower() for n in t.get("niches", [])}
            break
    if video_niche and track_niches and not any(
        video_niche in n or n in video_niche for n in track_niches
    ):
        _log(f"music design: niche mismatch — {slug!r} niches={track_niches} "
             f"vs video niche={video_niche!r} -> no music")
        slug = None
```

**Note:** This requires `video_meta` to have a `niche` key. Check what Stage 2 writes. The niche matching logic above is substring-based and may need tuning.

---

### Gap 4: `music_intensity=0` for key beat types

**Motivation:** Some moments demand silence. When a segment is `revelation_setup` or `climax`, music competes with the emotional impact. This should be enforced in code, not left to the LLM.

**What to change:**

In `src/vp/run.py` — add a post-processing pass over `doc.segments` after music design but before `build_master()`. A good place is just before Stage 6:

```python
_SILENT_BEAT_TYPES = {"revelation_setup", "climax"}
for seg in doc.segments:
    bt = getattr(seg, "beat_type", None) or ""
    if bt in _SILENT_BEAT_TYPES:
        seg.music_intensity = 0.0
```

**How `music_intensity` is used:** `build_master()` already reads `s.music_intensity` per segment and multiplies the music bed by it. Default is likely 0.4 or 1.0 — check `src/vp/schema/model.py`.

---

### Gap 5: Attribution flow to YouTube description

**Motivation:** CC BY 4.0 is a legal requirement. Kevin MacLeod tracks require credit.

**What to change:**

1. In `src/vp/pipeline/metadata.py` — the `MetadataStage.run()` call needs to receive the selected track's attribution string. Either pass it as a parameter or have `MetadataStage` read `out/music.json` directly.

2. In the description template (wherever `MetadataStage` builds the YouTube description) — append if non-null:
```python
attribution = catalog_track.get("attribution")
if attribution:
    description += f"\n\nMusic: {attribution}"
```

3. The lookup: given `slug = md["track"]`, find the track in catalog and read its `attribution` field.

**Simpler approach:** `MetadataStage.run()` already has access to `out` path. It can load `out/music.json`, get `track`, look it up in `catalog.json`, and append attribution. No argument changes needed.

---

## 10. Config Reference

**All keys that music integration reads from `doc.video_meta`:**

| Key | Set by | Used in |
|-----|--------|---------|
| `background_music_track` | `MusicDesigner.design()` | `master.py:_music_bed()` |
| `music_master_volume` | `MusicDesigner.design()` | `master.py:build_master()` |
| `music_fade_in_s` | `MusicDesigner.design()` (fixed 2.0) | `master.py:_music_bed()` |
| `music_fade_out_s` | `MusicDesigner.design()` (fixed 2.5) | `master.py:_music_bed()` |
| `music_loop_start_s` | `MusicDesigner.design()` (from catalog) | `master.py:_music_bed()` |
| `music_loop_end_s` | `MusicDesigner.design()` (from catalog) | `master.py:_music_bed()` |
| `music_duck_amount` | Not set by MusicDesigner; defaults to 0.7 | `master.py:build_master()` |
| `voice_master_volume` | Not set by MusicDesigner; defaults to 1.0 | `master.py:build_master()` |

**Per-segment key used in master.py:**
- `segment.music_intensity` — float multiplier [0.0, 1.0] applied to music bed for each segment's duration. Default should be checked in `schema/model.py`.

---

## 11. Resume / Checkpoint Pattern

`out/music.json` is the checkpoint file. Pattern is identical to `out/video.json` for Stage 2.

On `--resume`:
1. If `music.json` exists and is valid JSON with a `meta_patch` key → load, apply to `doc.video_meta`, skip API call
2. If loading fails for any reason → re-run `MusicDesigner.design()` fresh

On success (no resume): write `music.json` immediately after `design()` returns.

The checkpoint contains the full `design()` return dict, including `model`, `offline`, `reason` — useful for debugging and the manifest.

---

## 12. Testing

**Quick verification (no video needed):**

```python
import numpy as np
from pathlib import Path
from vp.audio_util import SR
from vp.pipeline.master import _music_bed

meta = {
    "background_music_track": "kevin-macleod-backbay-lounge",
    "music_master_volume": 0.18,
    "music_fade_in_s": 2.0,
    "music_fade_out_s": 2.5,
    "music_loop_start_s": 6.5,
    "music_loop_end_s": 261.0,
}
bed = _music_bed(360.0, meta, hook_s=4.0)

assert len(bed) == 360 * SR
assert np.all(bed[:int(4.0 * SR)] == 0.0), "hook not silent"
assert bed[int(6.0 * SR)] > 0.0, "music not started after hook+fade"
assert bed[int(350.0 * SR)] < bed[int(340.0 * SR)], "fade-out not happening"

# check no loop seams (no samples >3x the surrounding RMS)
window = int(0.5 * SR)
track_loop = int((261.0 - 6.5) * SR)
for seam_s in [6.5 + (261.0 - 6.5), 6.5 + 2*(261.0 - 6.5)]:
    if seam_s * SR < len(bed):
        n = int(seam_s * SR)
        local_rms = float(np.sqrt(np.mean(bed[max(0,n-window):n+window]**2)))
        peak = float(np.abs(bed[max(0,n-window):n+window]).max())
        assert peak < local_rms * 3.5, f"seam spike at {seam_s:.1f}s"

print("all assertions passed")
```

---

## 13. Source / License Reference

All tracks are sourced from [archive.org](https://archive.org) with declared licenses:

- **Kevin MacLeod** — [incompetech.com](https://incompetech.com), CC BY 4.0. Credit required.
- **Kai Hartwig** — Epic Tales Vol. 2, `archive.org/details/kai-hartwig-epic-tales-vol-2`, CC BY. Credit required.
- **Huma-Huma** (Clouds) — YouTube Audio Library, free to use with attribution.
- **Chris Haugen** (Fresh Fallen Snow) — YouTube Audio Library, free to use.
- **Unknown (royalty-free packs)** — No attribution needed.

Never use tracks from a source that doesn't explicitly state a CC or royalty-free license. "Free to download" is not a license. Archive.org items must have a license declared in the item metadata.
