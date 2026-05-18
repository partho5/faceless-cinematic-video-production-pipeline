"""Tiny dependency-light WAV helpers (stdlib wave + numpy only).

Used by the offline TTS stub and by tests so they don't need ffmpeg/pydub
just to make/inspect a clip.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 24000  # Gemini TTS output rate; keep stub consistent


def write_wav(path: Path, samples: np.ndarray, sr: int = SR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm16.tobytes())


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        ch = w.getnchannels()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767.0
    if ch == 2:
        a = a.reshape(-1, 2).mean(axis=1)
    return a, sr


def duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def synth_narration(text: str, *, wps: float = 2.6) -> np.ndarray:
    """A duration-accurate stand-in for a spoken line.

    Length ~= word_count / words-per-second. Content = low formant-ish tones
    gated per word so it's not pure silence (gives mastering/ducking and the
    proportional aligner real signal to work with). NOT intelligible — the
    real path uses Gemini; this only keeps the offline pipeline runnable.
    """
    words = [w for w in text.split() if w.strip()]
    n_words = max(1, len(words))
    total = n_words / wps
    t = np.arange(int(total * SR)) / SR
    sig = np.zeros_like(t)
    seg = total / n_words
    rng = np.random.default_rng(abs(hash(text)) % (2**32))
    for i in range(n_words):
        f0 = 95 + rng.uniform(-12, 18)  # male-ish narration band
        s0, s1 = i * seg, (i + 0.82) * seg  # gap between words
        mask = (t >= s0) & (t < s1)
        local = t[mask] - s0
        env = np.sin(np.pi * local / max(1e-6, (s1 - s0)))  # per-word swell
        sig[mask] += 0.30 * env * (
            np.sin(2 * np.pi * f0 * local)
            + 0.5 * np.sin(2 * np.pi * 2 * f0 * local)
            + 0.25 * np.sin(2 * np.pi * 3 * f0 * local)
        )
    return sig.astype(np.float32)


def pad_silence(samples: np.ndarray, pre_ms: int, post_ms: int) -> np.ndarray:
    pre = np.zeros(int(pre_ms / 1000 * SR), dtype=samples.dtype)
    post = np.zeros(int(post_ms / 1000 * SR), dtype=samples.dtype)
    return np.concatenate([pre, samples, post])
