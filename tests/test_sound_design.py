"""Editorial sound-effects system — unit + safety tests (plan §6).

Pure-Python, no network: the LLM is never called (offline spec or a
monkeypatched `_call_llm`). Covers the two non-creative safeties
(must-be-one-of-20, can't-bury-voice), anchor resolution, the seamless-
timeline invariant, and the empty-cues == clean-path guarantee.
"""
import numpy as np

from vp.audio_util import SR, read_wav, write_wav
from vp.config import ASSETS, get_config
from vp.pipeline import master as M
from vp.pipeline.align import Alignment, WordTime
from vp.pipeline.sound_design import (
    SoundDesigner, _PURPOSE, _resolve_anchor, catalog_ids, load_catalog,
)
from vp.schema.model import Segment, SoundFX
from vp.schema.validator import validate_sfx_cues
from vp.sample import load_sample_document


# ---- 1. catalog -----------------------------------------------------------
def test_catalog_has_20_ids_and_files_exist():
    cat = load_catalog()
    assert len(cat) == 20
    ids = catalog_ids()
    assert len(ids) == 20
    for e in cat:
        assert (ASSETS / "sfx" / e["file"]).exists(), e["file"]
    # the prompt's purpose map describes exactly the catalog, no more/less
    assert set(_PURPOSE) == ids


# ---- 2. must-be-one-of-20 (drop, no substitution, logged) -----------------
def test_validator_drops_off_catalog_without_substitution():
    allowed = {"impact-boom", "whoosh-fast"}
    cues = [
        {"segment_id": "s1", "sfx_id": "impact-boom", "intensity": "hard"},
        {"segment_id": "s2", "sfx_id": "explosion-mega", "intensity": "hard"},
        {"segment_id": "s3", "sfx_id": "whoosh-fast", "intensity": "ludicrous"},
        {"segment_id": "", "sfx_id": "impact-boom"},          # no segment
        "not-an-object",
    ]
    kept, warns = validate_sfx_cues(cues, allowed)
    # off-catalog id dropped entirely — NOT remapped to a near category
    assert [c["sfx_id"] for c in kept] == ["impact-boom", "whoosh-fast"]
    assert all(c["sfx_id"] in allowed for c in kept)
    assert any("explosion-mega" in w and "dropped" in w for w in warns)
    assert any("no substitution" in w for w in warns)
    # bad intensity clamped to the enum (the cue itself is kept)
    assert kept[1]["intensity"] == "normal"
    assert any("ludicrous" in w for w in warns)
    # no count cap: every in-vocabulary, well-formed cue survives
    assert len(kept) == 2


# ---- 3. anchor resolution (on_word + robust fallback) ---------------------
def test_anchor_on_word_resolves_else_segment_start():
    al = Alignment("s", [WordTime("This", 0.0, 0.2),
                         WordTime("changes", 0.2, 0.5),
                         WordTime("everything.", 0.5, 1.0)], 1.0, "stable-ts")
    t, note = _resolve_anchor({"kind": "on_word", "word": "everything"}, al, 2.0)
    assert abs(t - 0.5) < 1e-6 and note is None     # punctuation-insensitive

    t, note = _resolve_anchor({"kind": "on_word", "word": "nope"}, al, 2.0)
    assert t == 0.0 and note and "segment_start" in note   # never throws

    al2 = Alignment("s", [WordTime("go", 0.0, 0.1),
                          WordTime("go", 0.4, 0.5)], 1.0, "x")
    t, _ = _resolve_anchor({"kind": "on_word", "word": "go",
                            "occurrence": 2}, al2, 1.0)
    assert abs(t - 0.4) < 1e-6

    assert _resolve_anchor({"kind": "segment_start"}, al, 2.0)[0] == 0.0
    assert _resolve_anchor({"kind": "segment_end"}, al, 2.0)[0] == 2.0
    t, _ = _resolve_anchor({"kind": "at_fraction", "fraction": 0.25}, al, 2.0)
    assert abs(t - 0.5) < 1e-6
    # missing alignment must not throw
    assert _resolve_anchor({"kind": "on_word", "word": "x"}, None, 1.0)[0] == 0.0


# ---- helpers for the mix tests -------------------------------------------
def _voice_seg(tmp_path, *, amp=0.3, dur=1.0, fid="s1"):
    n = int(dur * SR)
    t = np.arange(n) / SR
    wav = tmp_path / f"{fid}.wav"
    write_wav(wav, (amp * np.sin(2 * np.pi * 200 * t)).astype(np.float32), SR)
    return Segment(id=fid, beat_type="hook", start=0.0, end=dur,
                   audio_path=str(wav))


# ---- 4. can't-bury-voice (the critical safety) ---------------------------
def test_cant_bury_voice_clamp_and_seamless(tmp_path, monkeypatch):
    seg = _voice_seg(tmp_path)

    seg.sound_fx = []
    M.build_master([seg], tmp_path / "a.wav", {}, loudnorm=False)
    a, _ = read_wav(tmp_path / "a.wav")

    # a deliberately ENORMOUS sfx (peak 5.0) — it must be tamed under voice
    monkeypatch.setattr(
        M, "_load_sfx",
        lambda kind: np.ones(int(0.2 * SR), np.float32) * 5.0)
    seg.sound_fx = [SoundFX(type="impact-boom", timing=0.4, intensity="hard")]
    M.build_master([seg], tmp_path / "b.wav", {}, loudnorm=False)
    b, _ = read_wav(tmp_path / "b.wav")

    at, clip = int(0.4 * SR), int(0.2 * SR)
    assert len(a) == len(b)                       # timeline immutable

    # everything OUTSIDE the cue window is byte-for-byte unchanged: the
    # narration is never reduced and the audio stays seamless
    assert np.array_equal(a[:at], b[:at])
    assert np.array_equal(a[at + clip:], b[at + clip:])

    # inside: the loud sfx is clamped strictly under the local voice
    m1 = min(len(a), max(at + clip, at + int(M._SFX_MEAS_MIN_S * SR)))
    v_rms = float(np.sqrt(np.mean(a[at:m1] ** 2)))
    ceiling = min(M._SFX_REL_CEIL * v_rms, M._SFX_ABS_CEIL)
    added_peak = float(np.abs(b[at:at + clip] - a[at:at + clip]).max())
    assert 0.0 < added_peak <= ceiling + 0.02     # can't-bury-voice
    assert added_peak <= M._SFX_ABS_CEIL + 1e-6   # global ceiling
    assert added_peak < float(np.abs(a[at:m1]).max())  # under the narration


# ---- 5. empty cues == clean no-SFX path, byte-stable ---------------------
def test_empty_cues_master_is_byte_stable(tmp_path):
    seg = _voice_seg(tmp_path)
    seg.sound_fx = []
    i1 = M.build_master([seg], tmp_path / "m1.wav", {}, loudnorm=False)
    b1 = (tmp_path / "m1.wav").read_bytes()
    seg.sound_fx = []
    i2 = M.build_master([seg], tmp_path / "m2.wav", {}, loudnorm=False)
    b2 = (tmp_path / "m2.wav").read_bytes()
    assert b1 == b2                               # deterministic
    assert i1["sfx_cues"] == 0 and i2["sfx_cues"] == 0
    assert i1["sfx_skipped_no_asset"] == 0


# ---- 6. design() owns sound_fx: strips legacy, offline => nothing --------
def test_design_strips_legacy_and_emits_nothing_when_empty(monkeypatch):
    doc = load_sample_document()
    assert any(s.sound_fx for s in doc.segments)   # sample carries legacy fx
    monkeypatch.setattr(SoundDesigner, "_call_llm", lambda self, doc: [])
    summary = SoundDesigner(get_config()).design(doc, {})
    assert summary["n_cues"] == 0
    assert all(s.sound_fx == [] for s in doc.segments)  # legacy stripped
    assert summary["model"] and "offline" in summary


# ---- 7. design() end-to-end: valid cue resolved + attached ---------------
def test_design_resolves_valid_cue_and_drops_unknown(monkeypatch):
    doc = load_sample_document()
    seg = doc.segments[0]
    word = seg.text_overlay.split()[0]
    al = Alignment(seg.id, [WordTime(word, 1.23, 1.40)], 1.0, "stable-ts")

    def fake(self, _doc):
        return [
            {"segment_id": seg.id, "sfx_id": "impact-boom",
             "anchor": {"kind": "on_word", "word": word},
             "intensity": "hard", "reason": "core reveal"},
            {"segment_id": seg.id, "sfx_id": "totally-made-up",
             "anchor": {"kind": "segment_start"}, "intensity": "soft",
             "reason": "x"},
        ]

    monkeypatch.setattr(SoundDesigner, "_call_llm", fake)
    summary = SoundDesigner(get_config()).design(doc, {seg.id: al})
    assert summary["n_cues"] == 1 and summary["n_dropped"] == 1
    fx = seg.sound_fx
    assert len(fx) == 1
    assert fx[0].type == "impact-boom" and fx[0].intensity == "hard"
    assert abs(fx[0].timing - 1.23) < 1e-6        # landed on the word onset
    assert all(f.type != "totally-made-up" for f in fx)  # no substitution
