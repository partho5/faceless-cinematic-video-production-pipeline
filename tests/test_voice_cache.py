"""Chapter cache: a pre-cached chapter must serve with ZERO API calls
(free-tier survival / resumable) and still produce per-segment slices.
"""
from dataclasses import dataclass

import numpy as np

from vp.audio_util import SR
from vp.config import get_config
from vp.pipeline import voice as V


@dataclass
class FakeSeg:
    id: str
    text_overlay: str
    audio_path: str = ""


def test_cache_hit_makes_no_api_call(tmp_path, monkeypatch):
    cfg = get_config()
    vs = V.VoiceStage(cfg)
    vs.scene, vs.context = "S", "C"

    segs = [FakeSeg("c1_seg1", "hello there"),
            FakeSeg("c1_seg2", "world now")]
    text = "hello there world now"
    key = V._cache_key(text, vs.voice, vs.spec.model, vs.scene, vs.context)

    # pre-populate the cache (as a prior run would have)
    pcm = (0.05 * np.random.default_rng(0).standard_normal(int(2.0 * SR))
           ).astype(np.float32)
    from vp.pipeline.align import Alignment, WordTime
    al = Alignment("c1", [WordTime("hello", 0.0, 0.4),
                          WordTime("there", 0.4, 0.8),
                          WordTime("world", 1.0, 1.4),
                          WordTime("now", 1.4, 1.8)], 1.0, "test")
    monkeypatch.setattr(V, "_CACHE_DIR", tmp_path / "cache")
    V._save_cached(key, pcm, al)

    # any API attempt must blow up — proving the cache path avoids it
    def boom(*a, **k):
        raise AssertionError("Gemini API called despite cache hit")

    monkeypatch.setattr(V.VoiceStage, "_synth", boom)

    class Doc:
        segments = segs

    out = tmp_path / "audio"
    vr = vs.synthesize(Doc(), out)

    assert vr.cached == 1 and vr.synthesized == 0
    assert (out / "c1_seg1.wav").exists() and (out / "c1_seg2.wav").exists()
