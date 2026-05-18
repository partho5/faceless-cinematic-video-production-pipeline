"""Offline neural voice (Piper) — real narration without any API/quota.

Used as the offline-mode voice when a Piper model is present in
assets/tts_voices/ (falls back to the tone stub otherwise). This makes the
keyless pipeline produce an actually-listenable narration instead of a
placeholder tone.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

from ..audio_util import SR
from ..config import ASSETS

VOICE_DIR = ASSETS / "tts_voices"


def available() -> Path | None:
    if not VOICE_DIR.exists():
        return None
    for onnx in sorted(VOICE_DIR.glob("*.onnx")):
        if onnx.with_suffix(".onnx.json").exists():
            return onnx
    return None


@lru_cache(maxsize=2)
def _voice(path: str):
    from piper import PiperVoice

    return PiperVoice.load(path)


def _resample(a: np.ndarray, src: int, dst: int = SR) -> np.ndarray:
    if src == dst or len(a) == 0:
        return a
    idx = (np.arange(int(len(a) * dst / src)) * src / dst).astype(int)
    return a[np.clip(idx, 0, len(a) - 1)]


# natural rate; gravitas comes from pauses/prosody, not slowed words
NARRATION_LENGTH_SCALE = 1.0


def piper_synth(text: str, length_scale: float = NARRATION_LENGTH_SCALE) -> np.ndarray:
    """Synthesize `text` -> mono float32 @ SR. Raises if Piper unavailable."""
    model = available()
    if model is None:
        raise RuntimeError("no Piper voice model in assets/tts_voices/")
    v = _voice(str(model))
    from piper import SynthesisConfig

    syn = SynthesisConfig(length_scale=length_scale)
    parts: list[np.ndarray] = []
    sr = SR
    for chunk in v.synthesize(text, syn_config=syn):
        parts.append(np.asarray(chunk.audio_float_array, dtype=np.float32))
        sr = chunk.sample_rate
    if not parts:
        return np.zeros(int(0.3 * SR), np.float32)
    audio = np.concatenate(parts)
    peak = float(np.abs(audio).max()) or 1.0
    return _resample(audio / peak * 0.95, sr, SR)
