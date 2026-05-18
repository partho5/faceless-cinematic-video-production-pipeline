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


def test_structural_error_is_rejected():
    doc = load_sample_document()
    doc.segments[2].end = doc.segments[2].start  # zero-length
    r = validate(doc)
    assert not r.ok
    assert any("end" in e for e in r.errors)


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
