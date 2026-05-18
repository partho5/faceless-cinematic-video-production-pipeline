"""Per-segment voiceover (planning/06).

Real path: Google Gemini TTS via `google-genai`, one call per segment, fixed
voice, `Scene: … Delivery: … Say: …` prompt, multi-take + auto-select.
Offline path (no GEMINI_API_KEY): duration-accurate synthetic narration so
the rest of the pipeline runs unchanged.

`pre_silence_ms`/`post_silence_ms` are inserted by US, not the model
(planning/06 §5), so pacing is exact and repeatable.
"""
from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..audio_util import SR, pad_silence, synth_narration, write_wav
from ..config import Config
from ..schema.model import Segment


def _offline_voice(text: str) -> np.ndarray:
    """Real Piper narration if a local model exists, else the tone stub."""
    try:
        from .tts_local import available, piper_synth

        if available():
            return piper_synth(text)
    except Exception:
        pass
    return synth_narration(text)


@dataclass
class TTSResult:
    segment_id: str
    audio_path: Path
    spoken_duration: float   # voice only (no pre/post silence)
    total_duration: float    # incl. engineered silence
    takes: int
    offline: bool


def build_prompt(seg: Segment, persona_prefix: str, prev_line: str | None) -> str:
    parts = [persona_prefix.strip().rstrip(".") + "."]
    if prev_line:
        parts.append(f"Previously said (do not speak): \"{prev_line}\".")
    parts.append(f"Scene: {seg.tts_scene}.")
    parts.append(f"Delivery: {seg.tts_delivery}.")
    parts.append(f"Say: {seg.text_overlay}")
    return " ".join(parts)


def _score_take(samples: np.ndarray, target_s: float) -> float:
    """Lower = better. Penalize duration drift, silence, clipping."""
    dur = len(samples) / SR
    rms = float(np.sqrt(np.mean(samples**2)) + 1e-9)
    clip = float(np.mean(np.abs(samples) > 0.99))
    return abs(dur - target_s) * 2.0 + (0.0 if rms > 0.02 else 5.0) + clip * 10.0


class TTSEngine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.spec = cfg.model("tts_audio")
        self.persona = self.spec.extra.get(
            "voice_persona_prefix",
            "A low, controlled male narrator, late-night documentary tone",
        )
        self.voice = self.spec.extra.get("voice_name", "Charon")
        self.default_takes = int(self.spec.params.get("takes", 3))
        self._client = None

    # -- real provider (lazy) ----------------------------------------------
    def _gemini(self):
        if self._client is None:
            from google import genai  # lazy: only when key present

            self._client = genai.Client(api_key=self.spec.api_key)
        return self._client

    def _synth_gemini(self, prompt: str, *, max_retries: int = 4) -> np.ndarray:
        import re
        import time

        from google.genai import types

        cfg = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.voice
                    )
                )
            ),
        )
        for attempt in range(max_retries):
            try:
                resp = self._gemini().models.generate_content(
                    model=self.spec.model, contents=prompt, config=cfg
                )
                data = resp.candidates[0].content.parts[0].inline_data.data
                return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32767.0
            except Exception as e:
                msg = str(e)
                if "429" not in msg and "RESOURCE_EXHAUSTED" not in msg:
                    raise
                if attempt == max_retries - 1:
                    raise
                m = re.search(r"retry in (\d+(?:\.\d+)?)s", msg) or \
                    re.search(r"'retryDelay': '(\d+)s'", msg)
                delay = min(70.0, float(m.group(1)) + 2 if m else 20 * (attempt + 1))
                time.sleep(delay)
        raise RuntimeError("unreachable")

    # -- public ------------------------------------------------------------
    def synthesize(
        self,
        seg: Segment,
        out_dir: Path,
        *,
        prev_line: str | None = None,
        takes: int | None = None,
    ) -> TTSResult:
        takes = self.default_takes if takes is None else takes
        prompt = build_prompt(seg, self.persona, prev_line)
        target = max(0.6, len(seg.text_overlay.split()) / 2.6)
        offline = self.spec.offline

        if offline:
            best = _offline_voice(seg.text_overlay)
            n_takes = 1
        else:
            candidates = []
            for _ in range(max(1, takes)):
                try:
                    candidates.append(self._synth_gemini(prompt))
                except Exception:  # G12: retry/fallback handled by multi-take
                    continue
            if not candidates:  # total provider failure -> safe stub
                best = _offline_voice(seg.text_overlay)
                offline, n_takes = True, 1
            else:
                best = min(candidates, key=lambda s: _score_take(s, target))
                n_takes = len(candidates)

        spoken = len(best) / SR
        from ..pacing import effective_silences

        pre_ms, post_ms = effective_silences(seg)
        full = pad_silence(best, pre_ms, post_ms)
        path = out_dir / f"{seg.id}.wav"
        write_wav(path, full)
        return TTSResult(
            segment_id=seg.id,
            audio_path=path,
            spoken_duration=round(spoken, 3),
            total_duration=round(len(full) / SR, 3),
            takes=n_takes,
            offline=offline,
        )


def synthesize_all(cfg: Config, segments: list[Segment], audio_dir: Path) -> list[TTSResult]:
    """Synthesize every segment; sets seg.audio_path. Prev-line continuity."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    eng = TTSEngine(cfg)
    results = []
    prev = None
    for seg in segments:
        res = eng.synthesize(seg, audio_dir, prev_line=prev)
        seg.audio_path = str(res.audio_path)
        results.append(res)
        prev = seg.text_overlay
    return results
