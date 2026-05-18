"""Stability guards for the new Layer-3/9/10 xforms.

The whole point of these effects is that they must NOT destabilise the
existing pipeline: identical output on the paths the old engine handled,
shape/dtype preserved, no black borders, deterministic.
"""
from dataclasses import dataclass, field

import numpy as np

from vp.fx.ambient import FilmAmbience, KenBurns


@dataclass
class FakeSeg:
    id: str
    duration: float = 3.0
    camera_motion: str = "locked_frame"
    beat_type: str = "tension_build"
    color_grade_override: str | None = None


@dataclass
class FakeDoc:
    segments: list
    video_meta: dict = field(default_factory=lambda: {"base_color_grade": "cold_isolation"})


class FakeCtx:
    def __init__(self, segs):
        self.doc = FakeDoc(segs)


def _frame():
    rng = np.random.default_rng(7)
    return (rng.random((180, 320, 3)) * 255).astype(np.uint8)


def test_kenburns_identity_when_explicit_camera_move():
    # segments that already animate must be byte-identical to the old path
    f = _frame()
    seg = FakeSeg("c1_seg1", camera_motion="crash_zoom")
    ctx = FakeCtx([seg])
    out = KenBurns()(seg, f, 1.0, ctx)
    assert out is f or np.array_equal(out, f)


def test_kenburns_static_clip_gets_motion_but_stays_valid():
    f = _frame()
    seg = FakeSeg("c1_seg1", camera_motion="locked_frame")
    ctx = FakeCtx([seg])
    a = KenBurns()(seg, f, 0.0, ctx)
    b = KenBurns()(seg, f, 2.9, ctx)
    assert a.shape == f.shape and a.dtype == np.uint8
    assert not np.array_equal(a, b)              # there IS motion
    # BORDER_REFLECT + scale>=1 -> no pure-black border introduced
    for edge in (a[0], a[-1], a[:, 0], a[:, -1]):
        assert edge.max() > 0
    # deterministic
    assert np.array_equal(b, KenBurns()(seg, f, 2.9, ctx))


def test_filmambience_identity_on_common_grade():
    f = _frame()
    seg = FakeSeg("c1_seg1", beat_type="tension_build",
                  color_grade_override="cold_isolation")
    ctx = FakeCtx([seg])
    out = FilmAmbience()(seg, f, 1.0, ctx)
    assert out is f or np.array_equal(out, f)


def test_filmambience_activates_on_opted_in_mood():
    f = _frame()
    seg = FakeSeg("c1_seg1", beat_type="reveal")
    ctx = FakeCtx([seg])
    out = FilmAmbience()(seg, f, 1.5, ctx)
    assert out.shape == f.shape and out.dtype == np.uint8
    assert not np.array_equal(out, f)            # leak applied
