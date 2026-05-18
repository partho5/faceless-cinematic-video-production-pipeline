"""Forced alignment: known segment text + its audio -> word timings.

This is NOT transcription — we already have the exact line, so it is a
forced-alignment problem (planning/06 §B). Real path: `stable-ts` (lazy,
heavy). Fallback (offline, or low alignment confidence per G9): proportional
timing weighted by word length across the *spoken* region (excluding the
engineered pre/post silence).

Output offsets are relative to the START of the segment's audio file
(i.e. they already include pre_silence). The renderer adds the segment's
absolute timeline start.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from ..audio_util import duration_seconds, read_wav

_NUM = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}


@dataclass
class WordTime:
    word: str
    start: float
    end: float


@dataclass
class Alignment:
    segment_id: str
    words: list[WordTime]
    confidence: float
    method: str  # "stable-ts" | "proportional"

    def to_dict(self) -> dict:
        return {
            "segment_id": self.segment_id,
            "method": self.method,
            "confidence": round(self.confidence, 3),
            "words": [asdict(w) for w in self.words],
        }

    def find(self, phrase: str) -> tuple[float, float] | None:
        """Span (start,end) of a (possibly multi-word) emphasis phrase."""
        toks = normalize_words(phrase)
        if not toks or not self.words:
            return None
        norm = [normalize_token(w.word) for w in self.words]
        for i in range(len(norm) - len(toks) + 1):
            if norm[i : i + len(toks)] == toks:
                return self.words[i].start, self.words[i + len(toks) - 1].end
        return None


def normalize_token(tok: str) -> str:
    tok = tok.lower().strip()
    tok = re.sub(r"[^\w']", "", tok)
    if tok.isdigit():
        tok = "".join(_NUM.get(c, c) for c in tok)
    return tok


def normalize_words(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.split():
        n = normalize_token(raw)
        if n:
            out.append(n)
    return out


def _proportional(seg_text: str, audio: Path, pre_ms: int, post_ms: int,
                   seg_id: str) -> Alignment:
    words = [w for w in seg_text.split() if w.strip()]
    total = duration_seconds(audio)
    pre = pre_ms / 1000.0
    spoken = max(0.05, total - pre - post_ms / 1000.0)
    # weight by char length (rough proxy for spoken length)
    weights = [max(1, len(re.sub(r"[^\w]", "", w))) for w in words] or [1]
    s = sum(weights)
    out, cursor = [], pre
    for w, wt in zip(words, weights):
        dur = spoken * wt / s
        out.append(WordTime(w, round(cursor, 4), round(cursor + dur, 4)))
        cursor += dur
    return Alignment(seg_id, out, confidence=0.5, method="proportional")


def _stable_ts(seg_text: str, audio: Path, seg_id: str) -> Alignment:
    import stable_whisper  # lazy / heavy

    model = stable_whisper.load_model("base")
    res = model.align(str(audio), seg_text, language="en")
    words: list[WordTime] = []
    confs: list[float] = []
    for seg in res.segments:
        for w in seg.words:
            words.append(WordTime(w.word.strip(), round(w.start, 4), round(w.end, 4)))
            confs.append(getattr(w, "probability", 1.0) or 1.0)
    conf = sum(confs) / len(confs) if confs else 0.0
    return Alignment(seg_id, words, confidence=round(conf, 3), method="stable-ts")


def align_segment(
    seg_id: str,
    seg_text: str,
    audio_path: Path,
    *,
    pre_ms: int = 0,
    post_ms: int = 0,
    offline: bool = True,
    min_conf: float = 0.55,
) -> Alignment:
    """Align one segment; fall back to proportional on offline/low-conf/error."""
    if not offline:
        try:
            a = _stable_ts(seg_text, audio_path, seg_id)
            n_expected = len(normalize_words(seg_text))
            if a.confidence >= min_conf and abs(len(a.words) - n_expected) <= 2:
                return a
        except Exception:
            pass  # G9: any failure -> robust proportional fallback
    return _proportional(seg_text, audio_path, pre_ms, post_ms, seg_id)
