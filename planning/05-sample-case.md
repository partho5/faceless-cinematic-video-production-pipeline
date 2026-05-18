# 05 — Sample Case (validation gate)

The build does **not** start until the user confirms this sample matches the
vision.

## Title

> **"The 7 Signs Someone Is Quietly Manipulating You"**

## Script (~6 min, ~900 words)

**[HOOK · 0:00–0:25]**
Most people never realize they are being manipulated. Not because they are
stupid. Because manipulation today looks like kindness. It looks like love.
It looks like a friend who cares too much. By the time you notice the
pattern, you have already lost something. Your money. Your time. Your sense
of self. This video will show you seven signs. Watch carefully. One of them
is probably happening to you right now.

**[SIGN 1 · 0:25–1:15] Love bombing**
The first sign is love bombing. In the beginning, this person made you feel
like the most important human alive. Constant texts. Endless compliments.
They called you their soulmate within weeks. It felt amazing. It was supposed
to feel amazing. Because that intensity was not love. It was a strategy. They
were installing dependency. Real connection grows slowly. Manipulation grows
fast and burns hot. If someone made you feel like a god in the first month,
ask yourself what they are preparing you for in the sixth.

**[SIGN 2 · 1:15–2:00] Gaslighting**
The second sign is gaslighting. You remember the conversation clearly. They
told you something. You acted on it. Now they deny ever saying it. They look
at you with concern. They suggest you are stressed. Tired. Maybe forgetful.
Slowly, you stop trusting your own memory. You start asking them what really
happened. That is the goal. When someone controls your version of reality,
they control you.

**[SIGN 3 · 2:00–2:45] Silent punishment**
The third sign is silent punishment. You did something they did not like.
They will not tell you what. Instead, they go quiet. Cold. Distant. You feel
the temperature drop. You start replaying every word, every action. You
apologize for things you are not even sure you did. That is the trap. Silence
is a weapon. And the person using it knows exactly how much it hurts.

**[SIGN 4 · 2:45–3:30] The favor economy**
The fourth sign is the favor economy. They do small things for you
constantly. Coffee. Rides. Compliments. You feel grateful. You feel close.
Then one day they ask for something big. Money. A favor that crosses your
boundaries. Access to something private. You say yes because saying no feels
impossible. They built that feeling over months. Every small favor was an
investment. And today they are collecting interest.

**[SIGN 5 · 3:30–4:15] Isolation**
The fifth sign is isolation. Slowly, they have opinions about your friends.
Your family makes them uncomfortable. They prefer it when it is just the two
of you. You start canceling plans. You stop returning calls. Your world gets
smaller. By the time you look up, this person is the only voice left in your
life. And now they can say anything, and you will believe it.

**[SIGN 6 · 4:15–5:00] The rescue cycle**
The sixth sign is the rescue cycle. They create a crisis. Then they solve it.
They make you feel scared. Then they make you feel safe. Over and over. Your
nervous system gets addicted to the relief. You confuse the person causing
the storm with the person ending it. They are the same person. They have
always been the same person.

**[SIGN 7 · 5:00–5:40] The subtle diminish**
The seventh sign is the one most people miss. After every interaction, you
feel slightly worse about yourself. Not always. Not dramatically. Just a
little. A small doubt. A small guilt. A small confusion. You leave the
conversation lighter in confidence than when you arrived. This is the
cleanest signal. Healthy people make you feel more like yourself.
Manipulators make you feel like a worse version of you.

**[CLOSING · 5:40–6:00]**
If you recognized even one sign, do not panic. Awareness is the first
defense. Manipulation only works in the dark. You just turned on the light.

## Full Video JSON (top-level)

```json
{
  "video_meta": {
    "title": "The 7 Signs Someone Is Quietly Manipulating You",
    "total_duration_seconds": 360,
    "target_audience": "USA_EU",
    "language": "english",
    "voice_id": "google_tts_male_low_serious",
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
    {"chapter_id": "hook", "start": 0.0, "end": 25.0, "intensity_curve": "rapid_peak", "segment_count": 6},
    {"chapter_id": "sign_1_love_bombing", "start": 25.0, "end": 75.0, "intensity_curve": "slow_build", "segment_count": 9},
    {"chapter_id": "sign_2_gaslighting", "start": 75.0, "end": 120.0, "intensity_curve": "creeping_dread", "segment_count": 8},
    {"chapter_id": "sign_3_silent_punishment", "start": 120.0, "end": 165.0, "intensity_curve": "cold_hold", "segment_count": 7},
    {"chapter_id": "sign_4_favor_economy", "start": 165.0, "end": 210.0, "intensity_curve": "slow_realization", "segment_count": 8},
    {"chapter_id": "sign_5_isolation", "start": 210.0, "end": 255.0, "intensity_curve": "tightening_spiral", "segment_count": 8},
    {"chapter_id": "sign_6_rescue_cycle", "start": 255.0, "end": 300.0, "intensity_curve": "rollercoaster", "segment_count": 9},
    {"chapter_id": "sign_7_subtle_diminish", "start": 300.0, "end": 340.0, "intensity_curve": "quiet_revelation", "segment_count": 7},
    {"chapter_id": "closing", "start": 340.0, "end": 360.0, "intensity_curve": "release_and_empower", "segment_count": 4}
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

## Sample Segment JSON — Sign 1 "Love Bombing" (0:25–1:15, 9 segments)

```json
{
  "chapter_id": "sign_1_love_bombing",
  "segments": [
    {
      "id": "s1_seg1", "beat_type": "chapter_open", "start": 25.0, "end": 28.5,
      "audio_path": "audio/s1_seg1.mp3",
      "text_overlay": "The first sign is love bombing.",
      "text_personality": "reveal", "text_color": "#F4E4C1", "text_position": "center",
      "text_animation_in": "split_character_reveal",
      "text_animation_emphasis": [{"word": "love bombing", "effect": "scale_up_glow", "color_shift": "#E84545"}],
      "text_animation_out": "fade_dissolve",
      "camera_motion": "slow_push_in", "camera_scale_start": 1.0, "camera_scale_end": 1.06,
      "clip_query_primary": "couple silhouette romantic golden hour",
      "clip_query_backup": "warm sunset two people close",
      "color_grade_override": "warm_comfort",
      "cut_in_type": "dip_from_black", "cut_out_type": "j_cut",
      "music_intensity": 0.35, "music_type": "tension_drone_minor_key_long",
      "sound_fx": [{"type": "soft_whoosh", "timing": 0.2, "volume": 0.5}],
      "grain_override": 0.18, "vignette_override": 0.3, "chromatic_aberration": 0.0
    },
    {
      "id": "s1_seg2", "beat_type": "setup", "start": 28.5, "end": 33.0,
      "audio_path": "audio/s1_seg2.mp3",
      "text_overlay": "In the beginning, this person made you feel like the most important human alive.",
      "text_personality": "clinical", "text_color": "#FFFFFF", "text_position": "bottom_third",
      "text_animation_in": "word_by_word_fade",
      "text_animation_emphasis": [{"word": "most important", "effect": "highlight_yellow_dim_rest"}],
      "text_animation_out": "shrink_to_dot",
      "camera_motion": "subtle_drift_right",
      "clip_query_primary": "phone screen text messages flooding notifications",
      "clip_query_backup": "smartphone hands typing fast",
      "color_grade_override": "warm_comfort",
      "cut_in_type": "hard_cut", "cut_out_type": "hard_cut",
      "music_intensity": 0.4,
      "sound_fx": [
        {"type": "phone_notification_subtle", "timing": 1.2, "volume": 0.3},
        {"type": "phone_notification_subtle", "timing": 2.4, "volume": 0.3}
      ],
      "grain_override": 0.2, "vignette_override": 0.3
    },
    {
      "id": "s1_seg3", "beat_type": "list_rhythm", "start": 33.0, "end": 38.5,
      "audio_path": "audio/s1_seg3.mp3",
      "text_overlay": "Constant texts. Endless compliments. Soulmate within weeks.",
      "text_personality": "aggressive", "text_color": "#FFFFFF", "text_position": "center",
      "text_animation_in": "staggered_line_drop",
      "text_animation_emphasis": [], "text_animation_out": "hard_cut",
      "camera_motion": "rapid_clip_montage",
      "montage_clips": [
        {"query": "couple laughing close up", "duration": 1.6},
        {"query": "flowers being delivered", "duration": 1.4},
        {"query": "romantic dinner candle", "duration": 1.5},
        {"query": "hands holding tightly", "duration": 1.0}
      ],
      "color_grade_override": "warm_comfort",
      "cut_in_type": "flash_frame_white", "cut_out_type": "smash_cut",
      "music_intensity": 0.55,
      "sound_fx": [{"type": "rising_swell", "timing": 0.0, "volume": 0.6}],
      "grain_override": 0.25, "vignette_override": 0.35
    },
    {
      "id": "s1_seg4", "beat_type": "shift_to_cold", "start": 38.5, "end": 41.0,
      "audio_path": "audio/s1_seg4.mp3",
      "text_overlay": "It felt amazing.",
      "text_personality": "whisper", "text_color": "#D8D8D8", "text_position": "center",
      "text_animation_in": "fade_from_blur",
      "text_animation_emphasis": [], "text_animation_out": "freeze_then_glitch",
      "camera_motion": "locked_frame",
      "clip_query_primary": "face smiling close up soft light",
      "clip_query_backup": "person happy looking up",
      "color_grade_override": "warm_comfort",
      "cut_in_type": "j_cut", "cut_out_type": "glitch_transition",
      "music_intensity": 0.3,
      "sound_fx": [{"type": "glitch_crackle", "timing": 2.2, "volume": 0.7}],
      "grain_override": 0.2, "vignette_override": 0.4, "chromatic_aberration": 0.0
    },
    {
      "id": "s1_seg5", "beat_type": "revelation_cold", "start": 41.0, "end": 44.5,
      "audio_path": "audio/s1_seg5.mp3",
      "text_overlay": "It was supposed to feel amazing.",
      "text_personality": "aggressive", "text_color": "#E84545", "text_position": "center",
      "text_animation_in": "word_slam_random_direction",
      "text_animation_emphasis": [{"word": "supposed", "effect": "shake_hard"}],
      "text_animation_out": "flash_to_dissolve",
      "camera_motion": "crash_zoom",
      "clip_query_primary": "shadow figure observing watching dark",
      "clip_query_backup": "silhouette behind glass",
      "color_grade_override": "threat",
      "cut_in_type": "smash_cut", "cut_out_type": "hard_cut",
      "music_intensity": 0.75,
      "sound_fx": [
        {"type": "bass_drop", "timing": 0.0, "volume": 0.85},
        {"type": "heartbeat_single", "timing": 2.0, "volume": 0.6}
      ],
      "grain_override": 0.4, "vignette_override": 0.65, "chromatic_aberration": 0.15
    },
    {
      "id": "s1_seg6", "beat_type": "explanation", "start": 44.5, "end": 48.0,
      "audio_path": "audio/s1_seg6.mp3",
      "text_overlay": "That intensity was not love. It was a strategy.",
      "text_personality": "clinical", "text_color": "#FFFFFF", "text_position": "bottom_third",
      "text_animation_in": "typewriter_character",
      "text_animation_emphasis": [{"word": "strategy", "effect": "scale_up_red"}],
      "text_animation_out": "fade_dissolve",
      "camera_motion": "imperceptible_drift",
      "clip_query_primary": "chess board pieces close up dark",
      "clip_query_backup": "strategy planning hand pointing",
      "color_grade_override": "clinical",
      "cut_in_type": "hard_cut", "cut_out_type": "j_cut",
      "music_intensity": 0.5,
      "sound_fx": [{"type": "typewriter_clicks", "timing": 0.0, "volume": 0.4}],
      "grain_override": 0.3, "vignette_override": 0.45
    },
    {
      "id": "s1_seg7", "beat_type": "deepening", "start": 48.0, "end": 52.0,
      "audio_path": "audio/s1_seg7.mp3",
      "text_overlay": "They were installing dependency.",
      "text_personality": "reveal", "text_color": "#E84545", "text_position": "center",
      "text_animation_in": "words_emerge_from_blur",
      "text_animation_emphasis": [{"word": "installing", "effect": "glitch_corruption_brief"}],
      "text_animation_out": "shatter_fall",
      "camera_motion": "slow_pull_out",
      "clip_query_primary": "person looking at phone obsessively dark room",
      "clip_query_backup": "lonely figure scrolling phone night",
      "color_grade_override": "surveillance",
      "cut_in_type": "hard_cut", "cut_out_type": "dip_to_black_brief",
      "music_intensity": 0.6,
      "sound_fx": [{"type": "tinnitus_ring_low", "timing": 2.5, "volume": 0.3}],
      "grain_override": 0.35, "vignette_override": 0.55, "chromatic_aberration": 0.08
    },
    {
      "id": "s1_seg8", "beat_type": "wisdom_contrast", "start": 52.0, "end": 58.0,
      "audio_path": "audio/s1_seg8.mp3",
      "text_overlay": "Real connection grows slowly. Manipulation grows fast and burns hot.",
      "text_personality": "reveal", "text_color": "#F4E4C1", "text_position": "center",
      "text_animation_in": "split_text_reveal",
      "text_animation_emphasis": [{"word": "burns hot", "effect": "ember_glow_red"}],
      "text_animation_out": "fade_dissolve",
      "camera_motion": "slow_push_in",
      "clip_query_primary": "candle flame slow motion close up",
      "clip_query_backup": "fire ember glowing dark",
      "color_grade_override": "warm_comfort_dark",
      "cut_in_type": "cross_dissolve_brief", "cut_out_type": "hard_cut",
      "music_intensity": 0.45,
      "sound_fx": [{"type": "fire_crackle_subtle", "timing": 0.0, "volume": 0.3}],
      "grain_override": 0.28, "vignette_override": 0.5
    },
    {
      "id": "s1_seg9", "beat_type": "direct_address", "start": 58.0, "end": 75.0,
      "audio_path": "audio/s1_seg9.mp3",
      "text_overlay": "If they made you feel like a god in month one, ask what they are preparing you for in month six.",
      "text_personality": "aggressive", "text_color": "#FFFFFF", "text_position": "center",
      "text_animation_in": "word_by_word_fade",
      "text_animation_emphasis": [
        {"word": "god", "effect": "scale_up_glow_white"},
        {"word": "preparing", "effect": "shake_red"},
        {"word": "month six", "effect": "highlight_red_dim_rest"}
      ],
      "text_animation_out": "hold_then_hard_cut",
      "camera_motion": "aggressive_push_in", "camera_scale_start": 1.0, "camera_scale_end": 1.15,
      "clip_query_primary": "person looking at camera intense eye contact dark",
      "clip_query_backup": "face close up dramatic lighting",
      "color_grade_override": "interrogation",
      "cut_in_type": "flash_frame_black", "cut_out_type": "dip_to_black",
      "music_intensity": 0.85,
      "sound_fx": [
        {"type": "bass_swell", "timing": 0.0, "volume": 0.7},
        {"type": "heartbeat_double", "timing": 8.0, "volume": 0.6},
        {"type": "deep_thud", "timing": 16.5, "volume": 0.8}
      ],
      "grain_override": 0.45, "vignette_override": 0.7, "chromatic_aberration": 0.12
    }
  ]
}
```

## What these 50 seconds produce

9 distinct segment treatments · 5 camera motions · 4 color grades (warm ↔
threat) · 6 text animation styles · 8 layered SFX · grain/vignette/chromatic
shifting per moment · cut rhythm: slow → montage → crash → held shot. Nothing
repeats; every segment serves its exact emotional beat.

## Decision point

If the user confirms this matches the vision → proceed to build: schema +
validator first, then the Python rendering engine (see
[02-system-architecture.md](02-system-architecture.md) build order).
