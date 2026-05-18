# 04 — JSON Schema Contract

Two levels: **video-level** (meta, chapters, global assets) and
**segment-level** (per 2–4s treatment). Claude Opus produces both; the
validator enforces them before render.

## Video-level

```json
{
  "video_meta": {
    "title": "string",
    "total_duration_seconds": 360,
    "target_audience": "USA_EU",
    "language": "english",
    "voice_name": "Gemini prebuilt voice (e.g. Charon)",
    "voice_persona_prefix": "A low, controlled male narrator, late-night documentary tone",
    "emotional_arc": "slow_dread_build_to_empowerment",
    "base_color_grade": "cold_isolation",
    "base_grain": 0.22,
    "base_vignette": 0.35,
    "base_chromatic_aberration": 0.05,
    "background_music_track": "tension_drone_minor_key_long",
    "music_master_volume": 0.18,
    "voice_master_volume": 1.0,
    "music_duck_amount": 0.7
  },
  "chapters": [
    {
      "chapter_id": "hook",
      "start": 0.0,
      "end": 25.0,
      "intensity_curve": "rapid_peak",
      "segment_count": 6
    }
  ],
  "global_assets": {
    "fonts": {
      "aggressive": "Anton-Regular.ttf",
      "clinical": "Inter-Bold.ttf",
      "whisper": "Cormorant-Italic.ttf",
      "reveal": "PlayfairDisplay-Bold.ttf",
      "handwritten": "Caveat-Bold.ttf"
    },
    "luts": {
      "cold_isolation": "luts/cold_isolation.cube",
      "threat": "luts/threat_red_crush.cube",
      "clinical": "luts/clinical_cyan.cube",
      "memory": "luts/memory_washed.cube",
      "interrogation": "luts/interrogation_harsh.cube",
      "revelation": "luts/revelation_bright.cube"
    },
    "sfx_library": "sfx/",
    "music_library": "music/"
  }
}
```

> Note: `voice_name` is a **Google Gemini TTS** prebuilt voice, fixed for the
> whole video. `voice_persona_prefix` is prepended to every segment's TTS
> prompt for timbre consistency. Emotion is steered per segment via
> `tts_scene` / `tts_delivery` (below) — see [06-voice-and-timing.md](06-voice-and-timing.md).

## Segment-level

```json
{
  "id": "s1_seg5",
  "beat_type": "revelation_cold",
  "start": 41.0,
  "end": 44.5,
  "audio_path": "audio/s1_seg5.mp3",
  "text_overlay": "It was supposed to feel amazing.",
  "tts_scene": "a dim room, the moment a comforting illusion breaks",
  "tts_delivery": "slow, low pitch, cold and deliberate, faint disappointment; stress 'supposed'",
  "pre_silence_ms": 350,
  "post_silence_ms": 120,
  "text_personality": "aggressive",
  "text_color": "#E84545",
  "text_position": "center",
  "text_animation_in": "word_slam_random_direction",
  "text_animation_emphasis": [
    {"word": "supposed", "effect": "shake_hard"}
  ],
  "text_animation_out": "flash_to_dissolve",
  "camera_motion": "crash_zoom",
  "camera_scale_start": 1.0,
  "camera_scale_end": 1.15,
  "clip_query_primary": "shadow figure observing watching dark",
  "clip_query_backup": "silhouette behind glass",
  "color_grade_override": "threat",
  "cut_in_type": "smash_cut",
  "cut_out_type": "hard_cut",
  "music_intensity": 0.75,
  "music_type": "tension_drone_minor_key_long",
  "sound_fx": [
    {"type": "bass_drop", "timing": 0.0, "volume": 0.85},
    {"type": "heartbeat_single", "timing": 2.0, "volume": 0.6}
  ],
  "grain_override": 0.4,
  "vignette_override": 0.65,
  "chromatic_aberration": 0.15
}
```

### Optional segment fields

- `montage_clips`: array of `{query, duration}` when `camera_motion` is
  `rapid_clip_montage` (multiple clips inside one segment).
- `camera_scale_start` / `camera_scale_end`: required for push/pull/crash motions.
- `text_animation_emphasis`: array of `{word, effect, color_shift?}`. The
  renderer matches each `word` to its forced-aligned span (see
  [06-voice-and-timing.md](06-voice-and-timing.md)) so the effect fires on
  the spoken word.
- `tts_scene` / `tts_delivery`: natural-language steering strings for the
  Gemini TTS prompt (the AI Studio "scene" / context behaviour, done via API
  prompt text since there is no structured param).
- `pre_silence_ms` / `post_silence_ms`: engineered pacing gaps inserted by
  Python (Gemini will not reliably hold dramatic pauses itself).

## Validator responsibilities

- Enum-check every styled field against the libraries in
  [03-variation-system.md](03-variation-system.md).
- Segment `start`/`end` contiguous and within chapter bounds; chapters cover
  `total_duration_seconds`.
- Every `audio_path` exists; every referenced font/LUT/sfx exists in
  `global_assets`.
- `sound_fx[].timing` within segment duration; volumes in [0,1].
- `tts_scene` / `tts_delivery` present and non-empty for every segment.
- `pre_silence_ms` / `post_silence_ms` ≥ 0 and sane (< 3000).
- Repair or reject before the render stage (never render malformed JSON).
