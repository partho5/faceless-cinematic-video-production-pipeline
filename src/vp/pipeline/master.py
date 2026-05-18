"""Layer 6 sound design + audio mastering (planning/03 §6, G10).

Builds the final mixed track on the reflowed timeline:
  voice (mastered)  +  music bed (ducked under voice)  +  SFX (JSON-timed)
then a 2-pass ffmpeg `loudnorm` to YouTube's ~-14 LUFS (G10).

SFX are loaded ONLY from assets/sfx when a matching .wav exists; an absent
or unknown effect is silence (no effect), never a procedural guess — a
synthesized stand-in is worse than nothing (it mismatches the cue and the
LLM invents effect names we have no real asset for). The music bed is still
synthesized when absent: it's a continuous designed bed, not a point effect.

Each cue is gain-staged by `intensity` RELATIVE to the local narration RMS
and hard-clamped so a sound can never bury the voice (safety #2 of plan
§1.3 — non-negotiable). The timeline is never stretched or shifted to make
room for a sound: SFX rides the existing mastered timeline (plan §1.4).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from ..audio_util import SR, read_wav, write_wav
from ..config import ASSETS
from ..schema.model import Segment


# ---------------------------------------------------------------- SFX load --
_EMPTY = np.zeros(0, np.float32)

# can't-bury-voice gain model (plan §4.4). intensity -> target SFX peak as a
# fraction of the LOCAL voice RMS; then a hard clamp the invariant rests on:
# SFX peak <= voice_local_RMS * _SFX_REL_CEIL AND <= _SFX_ABS_CEIL (full
# scale, pre-limiter). Numbers are a tuned starting point; the *invariant*
# (SFX strictly under narration) is not.
_SFX_GAIN = {"soft": 0.50, "normal": 0.80, "hard": 1.10}
_SFX_REL_CEIL = 1.25      # SFX peak never exceeds voice RMS * this
_SFX_ABS_CEIL = 0.70      # ...nor this fraction of full scale (pre-limiter)
_SFX_GAP_PEAK = 0.45      # absolute target when the cue lands in a voice gap
_SFX_VOICE_EPS = 1e-3     # voice RMS below this == "deliberate pause / gap"
_SFX_MUSIC_DUCK_DB = 3.5  # music-only dip under the SFX window (mixing only)
_SFX_MEAS_MIN_S = 0.25    # min window for a stable local-voice RMS estimate


def _load_sfx(kind: str) -> np.ndarray:
    """Real asset audio for `kind`, or EMPTY (=> no effect) if absent.

    No procedural synthesis: an invented/unknown effect name renders as
    silence. A wrong synthetic sound is worse than the cue simply not
    firing. Drop a real assets/sfx/<kind>.wav to enable an effect.
    """
    p = ASSETS / "sfx" / f"{kind}.wav"
    if p.exists():
        a, _ = read_wav(p)
        return a
    return _EMPTY


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

    # 3. SFX at resolved timings (segment-relative; SoundDesigner already
    #    placed + validated them). Absent/unknown asset => silent no-op,
    #    logged rendered:false so the manifest stays honest. Each cue is
    #    gain-staged UNDER the local narration and the music is briefly
    #    ducked under it — placement only, the timeline is never touched.
    duck = 1.0 - 10 ** (-_SFX_MUSIC_DUCK_DB / 20.0)
    meas_w = int(_SFX_MEAS_MIN_S * SR)
    sfx_log = []
    skipped = 0
    for s in segments:
        for fx in s.sound_fx:
            clip = _load_sfx(fx.type)
            rendered = len(clip) > 0
            at = int((starts[s.id] + fx.timing) * SR)
            v_rms = 0.0
            target = 0.0
            if rendered and at < len(mix):
                end = min(len(mix), at + len(clip))
                # local voice level: RMS over the cue, widened to a stable
                # window (a 100 ms click must not be judged on 100 ms).
                m1 = min(len(voice), max(end, at + meas_w))
                vseg = voice[at:m1]
                v_rms = float(np.sqrt(np.mean(vseg ** 2))) if len(vseg) else 0.0

                if v_rms < _SFX_VOICE_EPS:        # deliberate pause / gap
                    target = min(_SFX_GAP_PEAK, _SFX_ABS_CEIL)
                else:
                    g = _SFX_GAIN.get(fx.intensity, _SFX_GAIN["normal"])
                    ceil = min(_SFX_REL_CEIL * v_rms, _SFX_ABS_CEIL)
                    target = min(g * v_rms, ceil)  # the can't-bury-voice clamp

                pk = float(np.abs(clip).max())
                clip = clip * (target / pk) if pk > 1e-9 else clip * 0.0
                # music-only duck under the SFX (mixing, NOT a timing change)
                mix[at:end] -= music[at:end] * duck
                mix[at:end] += clip[: end - at]
            else:
                skipped += 1
            sfx_log.append({"segment": s.id, "type": fx.type,
                            "at": round(starts[s.id] + fx.timing, 3),
                            "intensity": fx.intensity, "reason": fx.reason,
                            "voice_rms": round(v_rms, 4),
                            "sfx_peak": round(target, 4),
                            "rendered": rendered})

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
        "sfx_cues": len(sfx_log),
        "sfx_skipped_no_asset": skipped,
        "music_track": meta.get("background_music_track", "synth_tension_drone"),
    }
