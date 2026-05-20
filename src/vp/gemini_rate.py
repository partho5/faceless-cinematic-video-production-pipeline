"""Per-key proactive rate limiter for Gemini API calls.

When gemini.tier = free in config.yaml, each API key is capped at
gemini.rpm requests per minute (Gemini free tier default: 10 RPM).
Paid tier or key absent: limiter is a no-op — zero behavior change.

A module-level singleton is shared by voice.py, llm.py, and
tts_gemini.py so TTS and LLM quota is counted together per key.
Effective system capacity scales linearly: N free-tier keys = N × RPM.
"""
from __future__ import annotations

import threading
import time
from collections import deque

GEMINI_FREE_TIER_RPM = 10


class PerKeyRateLimiter:
    """Sliding-window rate limiter with one independent window per API key."""

    def __init__(self, rpm: int):
        self._rpm = rpm
        self._windows: dict[str, deque[float]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._meta = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._rpm > 0

    def acquire(self, key: str) -> None:
        """Block until `key` has a free slot in its 60-second window.

        Records the dispatch timestamp immediately so concurrent threads
        sharing the same key each consume a distinct slot — no over-counting,
        no under-counting. Never raises.
        """
        if not self.enabled or not key:
            return
        lock = self._key_lock(key)
        with lock:
            window = self._windows[key]
            while True:
                now = time.time()
                while window and window[0] <= now - 60.0:
                    window.popleft()
                if len(window) < self._rpm:
                    window.append(now)
                    return
                # window full — sleep until oldest slot exits the 60 s window
                wait = window[0] + 60.0 - now
                time.sleep(max(0.05, wait))

    def _key_lock(self, key: str) -> threading.Lock:
        with self._meta:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
                self._windows[key] = deque()
            return self._locks[key]


# module-level singleton — lazily initialised from config on first acquire()
_limiter: PerKeyRateLimiter | None = None
_init_lock = threading.Lock()


def get_limiter() -> PerKeyRateLimiter:
    global _limiter
    if _limiter is not None:
        return _limiter
    with _init_lock:
        if _limiter is None:
            _limiter = _build()
    return _limiter


def _build() -> PerKeyRateLimiter:
    try:
        from .config import get_config
        raw = (get_config()._raw or {}).get("gemini", {}) or {}
        tier = str(raw.get("tier", "")).strip().lower()
        if tier != "free":
            return PerKeyRateLimiter(0)
        rpm = int(raw.get("rpm", GEMINI_FREE_TIER_RPM))
        print(f"[vp] Gemini free-tier rate limiter active: {rpm} RPM per key",
              flush=True)
        return PerKeyRateLimiter(rpm)
    except Exception:
        return PerKeyRateLimiter(0)
