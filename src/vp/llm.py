"""Anthropic call helper with Gemini fallback.

Primary provider: Anthropic. On ANY Anthropic failure the identical prompt
is retried via Google Gemini, rotating through all available GEMINI_API_KEY*
values (same keys the TTS stage uses). If every provider also fails, a
[API_ERROR:…] sentinel is printed so the GUI can surface a user-friendly
dialog.
"""
from __future__ import annotations

import os


def _collect_gemini_keys() -> list[str]:
    """All usable Gemini keys: GEMINI_API_KEY + GEMINI_API_KEYS comma-list
    + GEMINI_API_KEY_1…24.  Mirrors voice.collect_keys without importing it."""
    seen: set[str] = set()
    out: list[str] = []

    def _add(k: str) -> None:
        k = k.strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)

    _add(os.environ.get("GEMINI_API_KEY", ""))
    for k in os.environ.get("GEMINI_API_KEYS", "").split(","):
        _add(k)
    for i in range(1, 25):
        _add(os.environ.get(f"GEMINI_API_KEY_{i}", ""))
    return out


def _gemini_text(system: str, user: str) -> str:
    """Gemini text generation with key rotation.

    Cycles through every available key; a 429 / RESOURCE_EXHAUSTED skips
    to the next key. Any other error propagates immediately. Raises
    RuntimeError if all keys are rate-limited.
    """
    from google import genai
    from google.genai import types

    keys = _collect_gemini_keys()
    if not keys:
        raise RuntimeError("no Gemini API keys available for fallback")

    model = (
        os.environ.get("GEMINI_LLM_MODEL", "").strip() or "gemini-2.0-flash"
    )
    last_err: Exception | None = None
    for key in keys:
        try:
            client = genai.Client(api_key=key)
            resp = client.models.generate_content(
                model=model,
                contents=user,
                config=types.GenerateContentConfig(system_instruction=system),
            )
            return resp.text
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                last_err = e
                continue
            raise
    raise RuntimeError(
        f"all Gemini keys rate-limited during Anthropic fallback: {last_err}"
    )


def anthropic_message(spec, *, system: str, user: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=spec.api_key)
    params = dict(spec.params or {})
    kwargs = dict(
        model=spec.model,
        max_tokens=params.get("max_tokens", 4000),
        system=[{"type": "text", "text": system,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    if "temperature" in params:
        kwargs["temperature"] = params["temperature"]

    # ---------------------------------------------------------------- Anthropic
    anthropic_exc: Exception | None = None
    msg = None
    try:
        try:
            msg = client.messages.create(**kwargs)
        except anthropic.BadRequestError as e:
            if "temperature" in str(e).lower() and "temperature" in kwargs:
                kwargs.pop("temperature")
                msg = client.messages.create(**kwargs)
            else:
                raise
    except Exception as e:
        anthropic_exc = e

    if anthropic_exc is None:
        from .cost import record
        record(spec.model, getattr(msg, "usage", None),
               getattr(spec, "purpose", "llm"))
        return "".join(b.text for b in msg.content if b.type == "text")

    # ---------------------------------------------------------------- Gemini fallback
    print(
        f"[vp] warn: Anthropic failed ({str(anthropic_exc)[:80]}) — retrying via Gemini…",
        flush=True,
    )
    try:
        return _gemini_text(system, user)
    except Exception:
        # Both providers failed — print the specific sentinel so the GUI
        # shows the right user-friendly dialog, then re-raise original error.
        _err = str(anthropic_exc)
        _err_lower = _err.lower()
        if isinstance(anthropic_exc, anthropic.AuthenticationError):
            print(
                f"[vp] [API_ERROR:ANTHROPIC_KEY_INVALID] {_err[:200]}",
                flush=True,
            )
        elif isinstance(anthropic_exc, anthropic.PermissionDeniedError):
            etype = (
                "ANTHROPIC_CREDITS"
                if any(k in _err_lower
                       for k in ("credit", "billing", "balance", "payment"))
                else "ANTHROPIC_KEY_INVALID"
            )
            print(f"[vp] [API_ERROR:{etype}] {_err[:200]}", flush=True)
        elif isinstance(anthropic_exc, anthropic.BadRequestError):
            if any(k in _err_lower for k in (
                "credit balance", "billing", "upgrade", "purchase credits",
            )):
                print(
                    f"[vp] [API_ERROR:ANTHROPIC_CREDITS] {_err[:200]}",
                    flush=True,
                )
            else:
                print(
                    f"[vp] [API_ERROR:LLM_ALL_FAILED] {_err[:200]}",
                    flush=True,
                )
        else:
            print(
                f"[vp] [API_ERROR:LLM_ALL_FAILED] {_err[:200]}",
                flush=True,
            )
        raise anthropic_exc


def tts_framing(spec, topic: str) -> tuple[str, str] | None:
    """Best-effort: derive a niche-matched TTS scene + context from the
    topic so the user never has to write voice direction by hand.

    Returns (scene, context) or None on ANY failure / offline — the caller
    then keeps the channel-default framing from config. Never raises.
    """
    if getattr(spec, "offline", False):
        return None
    import json

    try:
        raw = anthropic_message(
            spec,
            system=(
                "You are a voiceover director. Given a video TOPIC, return "
                "STRICT JSON and nothing else: "
                '{"scene": "<one sentence naming the voice style and '
                'delivery>", "context": "<1-2 sentences: mood, emotional '
                'tone, pacing, the kind of pauses>"}. Match the voice to the '
                "topic (e.g. soothing for a bedtime story, intense for a "
                "psychology breakdown). No prose outside the JSON."
            ),
            user=f"TOPIC: {topic}",
        )
        blob = raw[raw.find("{"): raw.rfind("}") + 1]
        d = json.loads(blob)
        scene = str(d.get("scene", "")).strip()
        context = str(d.get("context", "")).strip()
        return (scene, context) if scene and context else None
    except Exception:
        return None
