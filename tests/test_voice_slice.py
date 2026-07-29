"""Regression: segment slicing must be a contiguous, lossless partition cut
at word onsets — never truncate trailing words, even when the aligner
tokenizes differently than naive .split() (contractions, em-dash, ASR
insert/delete). This reproduces the reported bug ("after 2 sentences every
sentence's last word got chopped, worse down the chapter").
"""
from dataclasses import dataclass

from vp.pipeline.align import WordTime
from vp.pipeline.voice import VoiceStage


@dataclass
class FakeSeg:
    id: str
    spoken_text: str
    audio_path: str = ""


def _words(pairs):
    # pairs: (word, start, end)
    return [WordTime(w, s, e) for w, s, e in pairs]


def test_contiguous_lossless_partition_no_drift():
    segs = [
        FakeSeg("c1_seg1", "It felt like love."),
        FakeSeg("c1_seg2", "It was a strategy."),
        FakeSeg("c1_seg3", "By the time you noticed."),
        FakeSeg("c1_seg4", "Around someone else's comfort."),
    ]
    # Aligner stream diverges from naive split: it drops the '.' tokens,
    # splits the contraction "else's" -> "else","s", and inserts a spurious
    # "uh". Naive counts would drift exactly like the real bug.
    words = _words([
        ("It", 0.00, 0.20), ("felt", 0.20, 0.45), ("like", 0.45, 0.65),
        ("love", 0.65, 1.10),                         # seg1 ends ~1.10
        ("It", 1.40, 1.55), ("was", 1.55, 1.70), ("a", 1.70, 1.78),
        ("strategy", 1.78, 2.60),                      # seg2 (chapter drift pt)
        ("uh", 2.65, 2.75),                            # ASR insertion
        ("By", 3.00, 3.15), ("the", 3.15, 3.25), ("time", 3.25, 3.45),
        ("you", 3.45, 3.60), ("noticed", 3.60, 4.30),  # seg3
        ("Around", 4.60, 4.95), ("someone", 4.95, 5.30),
        ("else", 5.30, 5.55), ("s", 5.55, 5.62),       # contraction split
        ("comfort", 5.62, 6.40),                       # seg4 last word
    ])
    total = 6.9  # audio runs past the last word (release + trailing pause)

    b = VoiceStage._segment_boundaries(segs, words, total)

    # invariants the OLD code violated
    assert b[0] == 0.0
    assert b[-1] == total                       # last seg reaches end of audio
    assert all(b[i] <= b[i + 1] for i in range(len(b) - 1))   # monotone
    assert all(b[i + 1] - b[i] >= 0.14 for i in range(len(segs)))  # non-empty
    # lossless: union of slices == whole audio
    assert abs(sum(b[i + 1] - b[i] for i in range(len(segs))) - total) < 1e-6

    # no drift: each segment starts at/just before its true first word, so
    # its LAST word is fully inside the slice (the bug truncated these).
    # seg2 "strategy" ends 2.60; seg3 starts >= ~3.0 -> "strategy" intact.
    assert b[2] >= 2.60 and b[2] <= 3.20
    # seg3 "noticed" ends 4.30; seg4 starts >= 4.30 -> "noticed" intact.
    assert b[3] >= 4.30 and b[3] <= 4.70
    # seg4 (last) runs to end -> "comfort" (ends 6.40) fully included.
    assert b[4] == total


def test_offline_proportional_identity_no_regression():
    # proportional aligner tokenizes exactly like .split() -> identity map;
    # boundaries must just track each segment's first word, lossless.
    segs = [FakeSeg("c1_seg1", "one two"), FakeSeg("c1_seg2", "three four"),
            FakeSeg("c1_seg3", "five six")]
    words = _words([("one", 0.0, 0.5), ("two", 0.5, 1.0),
                    ("three", 1.0, 1.5), ("four", 1.5, 2.0),
                    ("five", 2.0, 2.5), ("six", 2.5, 3.0)])
    b = VoiceStage._segment_boundaries(segs, words, 3.2)
    assert b[0] == 0.0 and b[-1] == 3.2
    assert abs(b[1] - 1.0) < 1e-6 and abs(b[2] - 2.0) < 1e-6
    assert all(b[i] <= b[i + 1] for i in range(len(b) - 1))
