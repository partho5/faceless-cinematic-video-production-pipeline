import copy

from vp.sample import load_sample_document
from vp.schema.model import ControlDocument
from vp.schema.validator import validate


def test_sample_document_validates():
    doc = load_sample_document()
    assert len(doc.segments) == 9
    assert doc.video_meta["title"].startswith("The 7 Signs")
    r = validate(doc)
    assert r.ok, str(r)


def test_unknown_enum_is_repaired_not_rejected():
    doc = load_sample_document()
    doc.segments[0].camera_motion = "teleport_through_time"
    r = validate(doc)
    assert r.ok, str(r)
    assert doc.segments[0].camera_motion == "locked_frame"
    assert any("teleport_through_time" in w for w in r.warnings)


def test_zero_length_segment_repaired_not_rejected():
    """end <= start is a planning hint, not a structural defect — G1 reflow
    overwrites planned times with measured audio. Validator must repair
    rather than reject so a single bad ordering hint can't kill the run."""
    doc = load_sample_document()
    s = doc.segments[2]
    s.end = s.start
    r = validate(doc)
    assert r.ok, str(r)
    assert s.end > s.start
    assert any("end" in w and "<=" in w for w in r.warnings)


def test_structural_error_is_rejected():
    """Genuine structural defects (chapters overlap, missing total_duration)
    must still hard-reject — only ordering/styling issues are repaired."""
    doc = load_sample_document()
    doc.video_meta["total_duration_seconds"] = 0
    r = validate(doc)
    assert not r.ok
    assert any("total_duration_seconds" in e for e in r.errors)


def test_sfx_volume_and_timing_clamped():
    doc = load_sample_document()
    s = doc.segments[0]
    s.sound_fx[0].volume = 5.0
    s.sound_fx[0].timing = 999.0
    validate(doc)
    assert 0.0 <= s.sound_fx[0].volume <= 1.0
    assert s.sound_fx[0].timing <= s.planned_duration + 0.05


def test_roundtrip_dict():
    doc = load_sample_document()
    d = doc.to_dict()
    doc2 = ControlDocument.from_dict(copy.deepcopy(d))
    assert len(doc2.segments) == len(doc.segments)
    assert doc2.segments[4].text_animation_emphasis[0].word == "supposed"


def test_check_audio_flags_missing():
    doc = load_sample_document()
    r = validate(doc, check_audio=True)
    assert not r.ok
    assert any("audio" in e for e in r.errors)


# ---- repair behavior the production pipeline depends on (regression locks) --

def test_empty_tts_scene_repaired_from_text_overlay():
    """Caption-only beats (tts_scene empty, text_overlay set) must NOT fail
    validation — the LLM sometimes drops tts_scene on overlay-style segments
    (credit cards, sentence continuations). Repair from text_overlay; warn."""
    doc = load_sample_document()
    s = doc.segments[3]
    s.tts_scene = ""
    r = validate(doc)
    assert r.ok, str(r)
    assert s.tts_scene == s.text_overlay
    assert any("tts_scene empty" in w for w in r.warnings)


def test_empty_tts_delivery_repaired_to_default():
    doc = load_sample_document()
    s = doc.segments[3]
    s.tts_delivery = ""
    r = validate(doc)
    assert r.ok, str(r)
    assert s.tts_delivery == "natural narration"
    assert any("tts_delivery empty" in w for w in r.warnings)


def test_empty_text_overlay_repaired_from_tts_scene():
    """Whisper/sound-design beats can run without an on-screen caption; the
    chapter-based TTS still needs SOMETHING to read, so fill text_overlay
    from tts_scene when only the caption is missing."""
    doc = load_sample_document()
    s = doc.segments[3]
    original_scene = s.tts_scene
    s.text_overlay = ""
    r = validate(doc)
    assert r.ok, str(r)
    assert s.text_overlay == original_scene
    assert any("text_overlay empty" in w for w in r.warnings)


def test_both_text_overlay_and_tts_scene_empty_get_placeholder():
    """Catastrophic case (LLM emitted a fully empty segment). Stamp a
    placeholder rather than reject so the chapter still produces audio
    and reflow() doesn't crash downstream."""
    doc = load_sample_document()
    s = doc.segments[3]
    s.text_overlay = ""
    s.tts_scene = ""
    r = validate(doc)
    assert r.ok, str(r)
    assert s.text_overlay and s.text_overlay.strip()
    assert s.tts_scene and s.tts_scene.strip()
    assert any("AND tts_scene empty" in w for w in r.warnings)


# ---- Stage-2 parser hardness (regression locks) -----------------------------

def test_stage2_parser_trailing_commas():
    from vp.pipeline.script_gen import _parse_segments_json
    raw = '[{"id":"a","text_overlay":"x",},{"id":"b","text_overlay":"y",},]'
    segs = _parse_segments_json(raw)
    assert len(segs) == 2
    assert segs[0]["id"] == "a"


def test_stage2_parser_salvages_around_broken_object():
    """A single malformed object must not lose the whole chapter."""
    from vp.pipeline.script_gen import _parse_segments_json
    raw = (
        '[{"id":"a","text_overlay":"x"},'
        '{"id":"b","text_overlay":"y" bogus}, '   # malformed
        '{"id":"c","text_overlay":"z"}]'
    )
    segs = _parse_segments_json(raw)
    # 'b' is dropped; 'a' and 'c' survive
    ids = {s["id"] for s in segs}
    assert "a" in ids and "c" in ids


def test_stage2_parser_handles_missing_closing_bracket():
    """LLM truncated mid-array — salvage every parseable object."""
    from vp.pipeline.script_gen import _parse_segments_json
    raw = '[{"id":"a","text_overlay":"x"},{"id":"b","text_overlay":"y"}'
    segs = _parse_segments_json(raw)
    assert len(segs) >= 1


# ---- per-chapter timestamp repair (regression locks) -----------------------

def test_repair_chapter_timestamps_zero_all():
    from vp.pipeline.script_gen import _repair_chapter_timestamps
    segs = [{"id": "c1_seg1", "start": 0, "end": 0},
            {"id": "c1_seg2", "start": 0, "end": 0}]
    specs = [{"chapter_id": "h", "start": 0.0, "end": 10.0}]
    _repair_chapter_timestamps(segs, specs)
    assert segs[0]["end"] > segs[0]["start"]
    assert segs[1]["start"] >= segs[0]["end"] - 0.01


def test_repair_chapter_timestamps_per_chapter_relative():
    """LLM emitted chapter 2 timestamps starting at 0 when chapter actually
    starts at 70.0 — re-base, preserve relative spacing."""
    from vp.pipeline.script_gen import _repair_chapter_timestamps
    segs = [
        {"id": "c2_seg1", "start": 0.0, "end": 3.0},
        {"id": "c2_seg2", "start": 3.0, "end": 6.0},
        {"id": "c2_seg3", "start": 6.0, "end": 9.0},
    ]
    specs = [
        {"chapter_id": "h", "start": 0.0, "end": 70.0},
        {"chapter_id": "b", "start": 70.0, "end": 135.0},
    ]
    _repair_chapter_timestamps(segs, specs)
    assert segs[0]["start"] == 70.0
    assert segs[1]["start"] == 73.0
    assert segs[2]["start"] == 76.0


# ---- mechanical spoken-beat splitter (regression lock) ----------------------
# Root cause of "partial script lines missed in output video": Stage-2 used to
# have an LLM *rewrite* each chapter into short beats, and it routinely
# paraphrased away entire clauses that didn't fit the target beat length —
# those words were never sent to TTS. split_spoken_chunks replaces that
# free-form rewrite with a deterministic slice: the LLM only attaches
# direction metadata afterward, so it can no longer make content vanish.

def test_split_spoken_chunks_is_lossless():
    from vp.pipeline.script_gen import split_spoken_chunks

    text = (
        "Researchers call prolonged sitting \"sitting disease\" — a term "
        "that didn't exist twenty years ago because desk jobs never used to "
        "demand eight straight hours in one position. Your spine evolved "
        "for constant micro-movement, not stillness. The damage from "
        "ignoring that isn't sudden; it stacks quietly, hour by hour, "
        "until a normal Tuesday turns into a day you can't stand up "
        "straight. And here's the part most people miss — by the time you "
        "feel the pain, the underlying damage has usually been building "
        "for months."
    )
    chunks = split_spoken_chunks(text)
    # every source word survives, in order, in exactly one chunk
    assert " ".join(chunks).split() == text.split()
    # and it actually produced more than one beat (the point of splitting)
    assert len(chunks) > 1


def test_split_spoken_chunks_handles_empty_and_short_text():
    from vp.pipeline.script_gen import split_spoken_chunks

    assert split_spoken_chunks("") == []
    assert split_spoken_chunks("Take care.") == ["Take care."]


def test_split_spoken_chunks_forces_cut_on_long_run_without_punctuation():
    from vp.pipeline.script_gen import split_spoken_chunks

    text = " ".join(f"word{i}" for i in range(40))  # no punctuation at all
    chunks = split_spoken_chunks(text)
    assert " ".join(chunks).split() == text.split()
    assert len(chunks) > 1  # must not collapse into one unspeakable beat


# ---- spoken_text is the TTS source of truth (regression lock) --------------

def test_spoken_text_empty_repaired_from_text_overlay():
    """Legacy/sample documents predate spoken_text; validator must backfill
    it from text_overlay rather than reject, so old fixtures keep working."""
    doc = load_sample_document()
    s = doc.segments[0]
    assert s.spoken_text == ""
    r = validate(doc)
    assert r.ok, str(r)
    assert s.spoken_text == s.text_overlay
    assert any("spoken_text empty" in w for w in r.warnings)


def test_spoken_text_untouched_when_already_set():
    """Once the mechanical splitter has populated spoken_text, validation
    must never overwrite it with a (possibly shortened) caption."""
    doc = load_sample_document()
    s = doc.segments[0]
    s.spoken_text = "the real, complete, mechanically-sliced narration"
    s.text_overlay = "a short caption"
    r = validate(doc)
    assert r.ok, str(r)
    assert s.spoken_text == "the real, complete, mechanically-sliced narration"
