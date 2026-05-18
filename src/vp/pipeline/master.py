"""Layer 6 sound design + audio mastering (planning/03 §6, G10).

Builds the final mixed track on the reflowed timeline:
  voice (mastered)  +  music bed (ducked under voice)  +  SFX (JSON-timed)
then a 2-pass ffmpeg `loudnorm` to YouTube's ~-14 LUFS (G10).

Music/SFX are loaded from assets/{music,sfx} when present, else synthesized
procedurally so the offline pipeline still gets a real designed mix.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from ..audio_util import SR, read_wav, write_wav
from ..config import ASSETS
from ..schema.model import Segment


# ----------------------------------------------------------- procedural SFX --
def _env(n: int, attack: float = 0.01, decay: float = 0.3) -> np.ndarray:
    t = np.arange(n) / SR
    a = np.clip(t / max(1e-4, attack), 0, 1)
    d = np.exp(-(np.clip(t - attack, 0, None)) / decay)
    return (a * d).astype(np.float32)


def _tone(f, dur, decay=0.3):
    n = int(dur * SR)
    t = np.arange(n) / SR
    return (np.sin(2 * np.pi * f * t) * _env(n, decay=decay)).astype(np.float32)


def _noise(dur, decay=0.2):
    n = int(dur * SR)
    return (np.random.default_rng(1).normal(0, 0.5, n) * _env(n, decay=decay)).astype(np.float32)


def _mix(*arrs: np.ndarray) -> np.ndarray:
    n = max(len(a) for a in arrs)
    out = np.zeros(n, np.float32)
    for a in arrs:
        out[: len(a)] += a
    return out


def _synth_sfx(kind: str) -> np.ndarray:
    k = kind.lower()
    if "bass" in k or "thud" in k or "deep" in k:
        return _mix(_tone(48, 0.6, 0.18), 0.5 * _tone(72, 0.5, 0.15))
    if "whoosh" in k or "swell" in k or "rising" in k:
        n = int(0.6 * SR); t = np.arange(n) / SR
        return (np.random.default_rng(2).normal(0, .5, n)
                * np.sin(np.pi * t / t[-1]) * 0.8).astype(np.float32)
    if "heartbeat" in k:
        b = _tone(55, 0.18, 0.08)
        gap = np.zeros(int(0.22 * SR), np.float32)
        return np.concatenate([b, gap, b * 0.8])
    if "glitch" in k or "static" in k or "crackle" in k:
        return _noise(0.3, 0.06)
    if "tinnitus" in k or "ring" in k:
        return _tone(3200, 0.8, 0.5) * 0.5
    if "phone" in k or "notification" in k:
        return _mix(_tone(880, 0.12, 0.06), _tone(1320, 0.12, 0.06))
    if "fire" in k or "ember" in k:
        return _noise(0.5, 0.25) * 0.4
    if "typewriter" in k or "click" in k:
        return _noise(0.05, 0.01)
    return _tone(220, 0.25, 0.12)  # generic accent


def _load_or_synth_sfx(kind: str) -> np.ndarray:
    for ext in (".wav",):
        p = ASSETS / "sfx" / f"{kind}{ext}"
        if p.exists():
            a, _ = read_wav(p)
            return a
    return _synth_sfx(kind)


def _music_bed(total_s: float) -> np.ndarray:
    p = ASSETS / "music" / "tension_drone.wav"
    if p.exists():
        a, _ = read_wav(p)
        return np.tile(a, int(np.ceil(total_s * SR / len(a))))[: int(total_s * SR)]
    n = int(total_s * SR)
    t = np.arange(n) / SR
    drone = (0.5 * np.sin(2 * np.pi * 55 * t)
             + 0.3 * np.sin(2 * np.pi * 82.4 * t)
             + 0.2 * np.sin(2 * np.pi * 110 * t * (1 + 0.002 * np.sin(0.3 * t))))
    return (drone * 0.25).astype(np.float32)


# ---------------------------------------------------------------- mastering --
def _envelope(x: np.ndarray, win: int) -> np.ndarray:
    p = np.abs(x)
    k = np.ones(win, np.float32) / win
    return np.convolve(p, k, mode="same")


def _compress(x: np.ndarray, thresh=0.25, ratio=3.0) -> np.ndarray:
    e = _envelope(x, int(0.01 * SR)) + 1e-6
    over = np.maximum(e, thresh)
    gain = (thresh + (over - thresh) / ratio) / over
    return x * gain


def _highpass(x: np.ndarray, prev=0.97) -> np.ndarray:
    y = np.empty_like(x)
    y[0] = x[0]
    y[1:] = x[1:] - x[:-1] + prev * x[:-1]  # cheap 1-pole HPF (rumble cut)
    return y


def build_master(
    segments: list[Segment],
    out_path: Path,
    meta: dict,
    *,
    loudnorm: bool = True,
) -> dict:
    """Mix voice+music+SFX on the reflowed timeline -> mastered out_path."""
    # 1. voice on the reflowed timeline (segments are back-to-back)
    voice_chunks, starts, cursor = [], {}, 0.0
    for s in segments:
        a, sr = read_wav(Path(s.audio_path))
        starts[s.id] = cursor
        voice_chunks.append(a)
        cursor += len(a) / SR
    voice = np.concatenate(voice_chunks) if voice_chunks else np.zeros(1, np.float32)
    total_s = len(voice) / SR

    voice = _compress(_highpass(voice)) * float(meta.get("voice_master_volume", 1.0))

    # 2. music bed, ducked under voice
    music = _music_bed(total_s)[: len(voice)]
    if len(music) < len(voice):
        music = np.pad(music, (0, len(voice) - len(music)))
    venv = _envelope(voice, int(0.05 * SR))
    venv = venv / (venv.max() + 1e-6)
    duck = float(meta.get("music_duck_amount", 0.7))
    music_gain = float(meta.get("music_master_volume", 0.18)) * (1.0 - duck * venv)
    # per-segment music_intensity scaling
    intens = np.ones(len(voice), np.float32)
    for s in segments:
        i0 = int(starts[s.id] * SR)
        i1 = min(len(voice), int((starts[s.id] + s.duration) * SR))
        intens[i0:i1] = s.music_intensity
    music = music * music_gain * intens

    mix = voice + music

    # 3. SFX at JSON timings (segment-relative)
    sfx_log = []
    for s in segments:
        for fx in s.sound_fx:
            clip = _load_or_synth_sfx(fx.type) * fx.volume
            at = int((starts[s.id] + fx.timing) * SR)
            end = min(len(mix), at + len(clip))
            if at < len(mix):
                mix[at:end] += clip[: end - at]
            sfx_log.append({"segment": s.id, "type": fx.type,
                            "at": round(starts[s.id] + fx.timing, 3),
                            "volume": fx.volume})

    # safety limiter
    peak = np.abs(mix).max()
    if peak > 1.0:
        mix = mix / peak * 0.98

    raw = out_path.with_suffix(".raw.wav")
    write_wav(raw, mix.astype(np.float32), SR)

    target_lufs = -14.0
    if loudnorm:
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(raw),
                 "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
                 "-ar", str(SR), str(out_path)],
                check=True, capture_output=True,
            )
            raw.unlink(missing_ok=True)
        except Exception:
            raw.replace(out_path)  # fall back to un-normalized but valid
    else:
        raw.replace(out_path)

    return {
        "path": str(out_path),
        "duration": round(total_s, 3),
        "target_lufs": target_lufs,
        "sfx_events": sfx_log,
        "music_track": meta.get("background_music_track", "synth_tension_drone"),
    }
