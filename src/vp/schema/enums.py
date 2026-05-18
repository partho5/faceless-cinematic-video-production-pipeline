"""Known vocabularies from planning/03 (variation system).

These are the *recognized* values. Unknown values are NOT fatal — the
renderer maps any unknown to a safe default and the validator records a
warning (planning/04: "repair or reject"; we repair styled fields and only
reject structural errors). Treating the lists as closed would make Claude's
creative variation brittle.
"""

TEXT_PERSONALITY = {"aggressive", "clinical", "whisper", "reveal", "handwritten"}

TEXT_POSITION = {"center", "bottom_third", "top_third", "lower_left", "upper_right"}

TEXT_ANIM_IN = {
    "word_by_word_fade", "typewriter_character", "word_slam_random_direction",
    "settle_with_bounce", "raindrop_fall", "materialize_from_blur",
    "split_character_reveal", "invisible_pen_stroke", "emerge_from_circles",
    "emerge_from_underline", "split_text_reveal", "words_emerge_from_blur",
    "staggered_line_drop", "fade_from_blur",
}

TEXT_ANIM_EMPHASIS = {
    "scale_up_settle", "scale_up_glow", "scale_up_glow_white", "scale_up_red",
    "brief_glow", "shake_hard", "shake_red", "color_shift", "highlight_yellow_dim_rest",
    "highlight_red_dim_rest", "background_dim", "emphasis_hold_rest_fade",
    "glitch_corruption_brief", "ember_glow_red",
}

TEXT_ANIM_OUT = {
    "shatter_fall", "burn_away", "dissolve_to_particles", "shrink_to_dot",
    "slide_off_reading", "flash_white_gone", "hard_cut", "inverse_typewriter",
    "fade_dissolve", "flash_to_dissolve", "freeze_then_glitch",
    "hold_then_hard_cut",
}

CAMERA_MOTION = {
    "locked_frame", "imperceptible_drift", "slow_push_in", "aggressive_push_in",
    "slow_pull_out", "crash_zoom", "dolly_zoom", "subtle_drift_left",
    "subtle_drift_right", "parallax", "whip_pan", "crane_down", "tilt_up_reveal",
    "light_shake", "heavy_shake", "breath_rhythm_shake", "recoil_on_impact",
    "dutch_angle_tilt", "spin_transition", "mirror_flip", "zoom_punch",
    "time_freeze_rotation", "rapid_clip_montage",
}

COLOR_GRADE = {
    "cold_isolation", "warm_comfort", "warm_comfort_dark", "clinical",
    "surveillance", "memory", "threat", "madness", "revelation", "death",
    "dream", "interrogation", "nostalgia",
}

CUT_TYPE = {
    "hard_cut", "j_cut", "l_cut", "smash_cut", "flash_frame_white",
    "flash_frame_black", "match_cut", "dip_to_black", "dip_to_black_brief",
    "dip_from_black", "glitch_transition", "cross_dissolve_brief",
    "cross_dissolve", "whip_pan_transition",
}

# Default substituted when a styled value is unknown (keeps render safe).
SAFE_DEFAULTS = {
    "text_personality": "clinical",
    "text_position": "center",
    "text_animation_in": "word_by_word_fade",
    "text_animation_out": "fade_dissolve",
    "camera_motion": "locked_frame",
    "cut_in_type": "hard_cut",
    "cut_out_type": "hard_cut",
}
