"""Continuous per-chapter voiceover (the corrected architecture).

Why: synthesizing each 2-4 s visual beat as an isolated TTS utterance and
stitching them with inserted silence sounds robotic (cold starts + terminal
intonation on every fragment + dead-air gaps). Instead we synthesize each
CHAPTER as one continuous, punctuated block so Gemini produces natural,
content-driven, varied pacing itself (validated against the Leda reference);
then we forced-align that audio and SLICE per visual segment at word
boundaries. Downstream (reflow/master/render) is unchanged — it just gets a
naturally-paced source.

Voice = Gemini `gemini-3.1-flash-tts-preview`, prebuilt voice `Leda`, with
the AI-Studio-style Scene/Sample-context framing the user approved.

Key rotation: a pool of API keys (primary + GEMINI_API_KEY_2..N and/or a
comma-separated GEMINI_API_KEYS) is round-robined per chapter call; a key
that 429s is parked for its server-suggested cooldown and the next live key
is used. This multiplies effective throughput ~linearly with #keys.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import hashlib
import json

from ..audio_util import SR, read_wav, write_wav
from ..config import CACHE, Config
from ..schema.model import ControlDocument, Segment
from .align import Alignment, WordTime, align_segment, normalize_token

# chapter-audio + alignment cache (free-tier survival: re-runs / repairs /
# render crashes cost ZERO Gemini calls; resumable without re-spend).
_CACHE_DIR = CACHE / "tts_chapters"


# --------------------------------------------------------------- key pool ---
class _Key:
    __slots__ = ("value", "cool_until")

    def __init__(self, value: str):
        self.value = value
        self.cool_until = 0.0


class KeyPool:
    def __init__(self, keys: list[str]):
        seen, uniq = set(), []
        for k in keys:
            k = (k or "").strip()
            if k and k not in seen:
                seen.add(k)
                uniq.append(_Key(k))
        if not uniq:
            raise RuntimeError("no GEMINI API keys available")
        self._keys = uniq
        self._rr = 0
        self._clients: dict[str, object] = {}

    @property
    def size(self) -> int:
        return len(self._keys)

    def _client(self, key: str):
        if key not in self._clients:
            from google import genai

            self._clients[key] = genai.Client(api_key=key)
        return self._clients[key]

    def acquire(self):
        """Return (key_obj, client), waiting out cooldowns if every key is hot.

        Blocking on a cooldown is NOT a failed attempt — the caller's retry
        budget must only count genuine API failures, never time spent here
        (see _synth). Loop, don't recurse, so a long 429 storm can't blow
        the stack.
        """
        n = len(self._keys)
        while True:
            now = time.time()
            for i in range(n):
                k = self._keys[(self._rr + i) % n]
                if k.cool_until <= now:
                    self._rr = (self._rr + i + 1) % n
                    return k, self._client(k.value)
            # all cooling -> sleep until the soonest one frees, then re-scan
            soonest = min(k.cool_until for k in self._keys)
            time.sleep(max(1.0, soonest - now))

    @staticmethod
    def park(key: _Key, err: str) -> None:
        m = re.search(r"retry in (\d+(?:\.\d+)?)s", err) or \
            re.search(r"'retryDelay': '(\d+)s'", err)
        delay = float(m.group(1)) + 2 if m else 30.0
        key.cool_until = time.time() + min(delay, 90.0)


def collect_keys(cfg: Config, spec) -> list[str]:
    """All usable Gemini keys: primary + comma list + GEMINI_API_KEY_<n>.

    Numbered scan starts at 1 (GEMINI_API_KEY_1) and tolerates gaps, so any
    of GEMINI_API_KEY / _1 / _2 / ... / GEMINI_API_KEYS=a,b,c works. Empty
    entries dropped, order preserved (KeyPool also de-dupes).
    """
    keys: list[str] = []
    if spec.api_key_env:
        keys.append(cfg.env(spec.api_key_env))
    rot_env = spec.extra.get("api_key_rotation_env", "GEMINI_API_KEYS")
    keys += cfg.env(rot_env).split(",")
    for i in range(1, 25):                       # _1.._24, gap-tolerant
        v = cfg.env(f"GEMINI_API_KEY_{i}")
        if v:
            keys.append(v)
    return [k for k in (s.strip() for s in keys) if k]


# --------------------------------------------------------------- synthesis --
@dataclass
class VoiceResult:
    chapters: int
    segments: int
    key_count: int
    offline: bool
    cached: int = 0          # chapters served from cache (0 API calls)
    synthesized: int = 0     # chapters that hit the Gemini API


def _cache_key(text: str, voice: str, model: str,
               scene: str, context: str, language: str = "") -> str:
    """Invalidates if the spoken text OR any prompt-affecting param changes."""
    h = hashlib.sha1()
    for part in (model, voice, scene, context, language, text):
        h.update((part or "").strip().encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _load_cached(key: str):
    """-> (pcm, Alignment) if both cached, (pcm, None), or (None, None)."""
    wav = _CACHE_DIR / f"{key}.wav"
    if not wav.exists():
        return None, None
    try:
        pcm, _ = read_wav(wav)
    except Exception:
        return None, None
    al = None
    aj = _CACHE_DIR / f"{key}.json"
    if aj.exists():
        try:
            d = json.loads(aj.read_text(encoding="utf-8"))
            al = Alignment(
                segment_id=d.get("segment_id", key),
                words=[WordTime(w["word"], w["start"], w["end"])
                       for w in d["words"]],
                confidence=d.get("confidence", 1.0),
                method=d.get("method", "cached"),
            )
        except Exception:
            al = None
    return pcm, al


def _save_cached(key: str, pcm, al: Alignment) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    write_wav(_CACHE_DIR / f"{key}.wav", pcm, SR)
    try:
        (_CACHE_DIR / f"{key}.json").write_text(json.dumps(al.to_dict()),
                                                encoding="utf-8")
    except Exception:
        pass


def _chapter_of(seg: Segment) -> str:
    return seg.id.split("_seg")[0] if "_seg" in seg.id else seg.id


_EMOTIONAL_SUFFIX = (
    "Important: deliver with strong emotional inflection. Use a wide "
    "pitch range — clear lifts on reveals, distinct drops on landings, "
    "real weight on key beats. Never flat, never monotone. Trust the "
    "silences after each emotional moment. The voice must carry the "
    "meaning, not just the words."
)


def _prompt(spec, scene: str, context: str, text: str,
            *, highly_emotional: bool = False) -> str:
    base = f"Scene: {scene} Sample context: {context}"
    if highly_emotional:
        base = f"{base} {_EMOTIONAL_SUFFIX}"
    return f"{base} Say: {text}"


class VoiceStage:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.spec = cfg.model("tts_audio")
        self.scene = (cfg_get(cfg, "tts_scene")
                      or self.spec.extra.get("tts_scene", ""))
        self.context = (cfg_get(cfg, "tts_context")
                        or self.spec.extra.get("tts_context", ""))
        self.voice = self.spec.extra.get("voice_name", "Leda")
        self.language: str = ""   # BCP-47 code; "" = let Gemini auto-detect
        # Channel-level emotional-delivery toggle: when true, _prompt appends
        # a strong-inflection clause to every chapter's TTS call so the
        # voice carries real weight on emotional beats. See cfg accessor.
        self.highly_emotional: bool = cfg.tts_highly_emotional

    def _synth(self, pool: KeyPool, text: str) -> np.ndarray:
        from google.genai import types

        speech_kw: dict = {}
        if self.language:
            speech_kw["language_code"] = self.language
        try:
            speech_cfg = types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.voice)),
                **speech_kw,
            )
        except TypeError:
            speech_cfg = types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.voice)))
        gcfg = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=speech_cfg,
        )
        prompt = _prompt(self.spec, self.scene, self.context, text,
                         highly_emotional=self.highly_emotional)
        last = None
        # Budget counts GENUINE failed API calls only. acquire() may block on
        # cooldowns without consuming this, so a transient 429 storm waits
        # (keys free in seconds) instead of prematurely raising; the cap is
        # still finite so a permanent daily-quota exhaustion can't hang.
        max_failures = max(8, pool.size * 3)
        failures = 0
        from ..gemini_rate import get_limiter
        _rate = get_limiter()
        while failures < max_failures:
            key, client = pool.acquire()
            _rate.acquire(key.value)
            try:
                r = client.models.generate_content(
                    model=self.spec.model, contents=prompt, config=gcfg)
                data = r.candidates[0].content.parts[0].inline_data.data
                return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32767.0
            except Exception as e:  # 429 -> park key; 500 -> rotate; else re-raise
                last = e
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    KeyPool.park(key, err_str)
                    failures += 1
                    continue
                if "500" in err_str or "INTERNAL" in err_str or \
                        "ServerError" in type(e).__name__:
                    # Transient server error — rotate to next key, no cooldown
                    failures += 1
                    continue
                err_lower = err_str.lower()
                if any(c in err_lower for c in (
                    "401", "unauthenticated", "api_key_invalid",
                    "permission_denied", "invalid api key", "api key not valid",
                )):
                    print(
                        f"[vp] [API_ERROR:GEMINI_KEY_INVALID] {err_str[:200]}",
                        flush=True,
                    )
                raise
        print(
            f"[vp] [API_ERROR:GEMINI_QUOTA] {failures} attempts all rate-limited",
            flush=True,
        )
        raise RuntimeError(
            f"all keys exhausted after {failures} rate-limited attempts: {last}")

    def synthesize(self, doc: ControlDocument, audio_dir: Path) -> VoiceResult:
        audio_dir.mkdir(parents=True, exist_ok=True)

        # online iff ANY usable key exists (primary OR rotation keys) — do
        # NOT trust spec.offline, which only checks the primary env var and
        # would wrongly go offline when only GEMINI_API_KEY_1.. are set.
        keys = collect_keys(self.cfg, self.spec)
        if not keys:
            from .tts_gemini import synthesize_all
            synthesize_all(self.cfg, doc.segments, audio_dir)
            return VoiceResult(0, len(doc.segments), 0, offline=True)

        pool = KeyPool(keys)

        # group segments by chapter, preserving order
        order: list[str] = []
        groups: dict[str, list[Segment]] = {}
        for s in doc.segments:
            c = _chapter_of(s)
            if c not in groups:
                groups[c] = []
                order.append(c)
            groups[c].append(s)

        cached = synthesized = 0
        for ch in order:
            segs = groups[ch]
            text = " ".join(s.text_overlay.strip()
                            for s in segs if s.text_overlay).strip()
            if not text:
                continue
            key = _cache_key(text, self.voice, self.spec.model,
                             self.scene, self.context, self.language)
            pcm, al = _load_cached(key)
            if pcm is not None:                       # cache hit -> 0 API calls
                cached += 1
            else:                                     # miss -> one Gemini call
                pcm = self._synth(pool, text)
                synthesized += 1
            ch_wav = audio_dir / f"_chapter_{ch}.wav"
            write_wav(ch_wav, pcm, SR)
            if al is None:                            # align if not cached
                al = align_segment(ch, text, ch_wav, offline=False)
            _save_cached(key, pcm, al)                 # resumable, no re-spend
            self._slice(ch, segs, pcm, al, audio_dir)

        return VoiceResult(len(order), len(doc.segments), pool.size,
                           offline=False, cached=cached,
                           synthesized=synthesized)

    @staticmethod
    def _segment_boundaries(segs, words, total_dur):
        """Time (s) where each segment's audio STARTS, as a contiguous,
        non-overlapping, lossless partition of the chapter read.

        We only ever locate each segment's FIRST word and cut there; we never
        compute a segment END (that was the bug — end indices drifted because
        `.split()` token counts != the aligner's tokenization, so trailing
        words got truncated, worse down the chapter). Cutting only at the
        next segment's first-word onset means: union of slices == whole
        audio, no word is ever acoustically chopped, and a mapping error can
        at worst shift a boundary word by one (visual sync), never truncate.

        Mapping our tokens -> aligner tokens uses a monotonic difflib map,
        robust to contraction/punctuation/em-dash tokenization differences
        and ASR insert/delete. In offline/proportional mode the two token
        streams are identical -> identity map -> unchanged behavior.
        """
        import difflib

        # flat normalized tokens + which segment each came from
        flat, owner, first_flat = [], [], [None] * len(segs)
        for si, s in enumerate(segs):
            toks = [t for t in (normalize_token(x)
                                for x in (s.text_overlay or "").split()) if t]
            if toks and first_flat[si] is None:
                first_flat[si] = len(flat)
            for t in toks:
                flat.append(t)
                owner.append(si)

        aw = [normalize_token(w.word) for w in words]
        n_aw = len(aw)

        # monotonic map: flat index -> aligned-word index
        fmap = [0] * len(flat)
        if flat and aw:
            sm = difflib.SequenceMatcher(a=flat, b=aw, autojunk=False)
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                span = max(1, i2 - i1)
                for k, i in enumerate(range(i1, i2)):
                    if tag == "equal":
                        fmap[i] = j1 + (i - i1)
                    elif j2 > j1:                       # replace
                        fmap[i] = j1 + (k * (j2 - j1)) // span
                    else:                               # delete (no aw word)
                        fmap[i] = j1
            run = 0                                     # enforce monotonic
            for i in range(len(fmap)):
                run = max(run, fmap[i])
                fmap[i] = min(run, n_aw - 1)

        # boundary[i] = start time of segment i; boundary[n] = end of audio
        b = [0.0] * (len(segs) + 1)
        b[len(segs)] = total_dur
        prev = 0.0
        for i in range(len(segs)):
            if i == 0:
                t = 0.0                                 # keep any lead-in
            elif first_flat[i] is not None and flat and aw:
                t = words[fmap[first_flat[i]]].start
            else:
                t = prev                                # empty seg -> tiny
            t = min(max(t, prev), total_dur)
            b[i] = t
            prev = t
        # guarantee strictly increasing with a minimum length per segment
        MIN = 0.15
        for i in range(len(segs)):
            if b[i + 1] - b[i] < MIN:
                b[i + 1] = min(total_dur, b[i] + MIN)
            if b[i + 1] < b[i]:
                b[i + 1] = b[i]
        return b

    # cut each visual segment out of the continuous chapter read; cuts land
    # only at word ONSETS (low-energy) so nothing is truncated.
    def _slice(self, ch, segs, pcm, al, audio_dir):
        words = al.words
        total_dur = len(pcm) / SR
        b = self._segment_boundaries(segs, words, total_dur)
        fade = max(1, int(0.008 * SR))  # ~8 ms click guard only
        for i, s in enumerate(segs):
            a = max(0, min(len(pcm), int(b[i] * SR)))
            e = max(a + 1, min(len(pcm), int(b[i + 1] * SR)))
            clip = pcm[a:e].copy()
            if len(clip) > 3 * fade:
                ramp = np.linspace(0, 1, fade, dtype=np.float32)
                clip[:fade] *= ramp
                clip[-fade:] *= ramp[::-1]
            p = audio_dir / f"{s.id}.wav"
            write_wav(p, clip if len(clip) else np.zeros(int(0.2 * SR),
                                                         np.float32), SR)
            s.audio_path = str(p)


def cfg_get(cfg: Config, key: str):
    """video_meta override hook (per-video scene/context), else None."""
    try:
        return cfg._raw.get("video_meta", {}).get(key)  # type: ignore[attr-defined]
    except Exception:
        return None
