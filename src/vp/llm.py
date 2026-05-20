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
    + GEMINI_API_KEY_1…24.  Mirrors voice.collect_keys without importing it.

    Config loads .env into its own dict, NOT into os.environ, so we must
    also parse the .env file directly — otherwise keys set only in .env are
    invisible here.
    """
    from .config import ROOT, _parse_env_file

    env: dict[str, str] = {**os.environ}
    env.update(_parse_env_file(ROOT / ".env"))

    seen: set[str] = set()
    out: list[str] = []

    def _add(v: str) -> None:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)

    _add(env.get("GEMINI_API_KEY", ""))
    for k in env.get("GEMINI_API_KEYS", "").split(","):
        _add(k)
    for i in range(1, 25):
        _add(env.get(f"GEMINI_API_KEY_{i}", ""))
    return out


def _gemini_text(system: str, user: str) -> str:
    """Gemini text generation with key + model rotation.

    Tries every key on every model (Pro → Flash) before giving up.
    Rate-limit errors (429 / RESOURCE_EXHAUSTED) skip to the next key.
    All other per-key errors are recorded and also skipped so that a bad
    key or a transient error on one key does not abort the entire fallback.
    Raises RuntimeError only when every key × model combination has failed.
    """
    from google import genai
    from google.genai import types

    keys = _collect_gemini_keys()
    if not keys:
        raise RuntimeError("no Gemini API keys available for fallback")

    # Quality cascade: Pro → Flash. Override via GEMINI_LLM_MODEL env var.
    override = os.environ.get("GEMINI_LLM_MODEL", "").strip()
    models = [override] if override else ["gemini-2.5-pro", "gemini-2.5-flash"]

    from .gemini_rate import get_limiter
    _rate = get_limiter()
    last_err: Exception | None = None
    for model in models:
        for key in keys:
            try:
                _rate.acquire(key)
                client = genai.Client(api_key=key)
                resp = client.models.generate_content(
                    model=model,
                    contents=user,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        response_modalities=["TEXT"],
                    ),
                )
                text = resp.candidates[0].content.parts[0].text
                if not text:
                    raise ValueError("empty response from Gemini")
                print(f"[vp] Gemini fallback succeeded via {model}", flush=True)
                return text
            except Exception as e:
                last_err = e
                print(
                    f"[vp] Gemini key failed ({model}): {str(e)[:120]} — trying next…",
                    flush=True,
                )
                continue
    raise RuntimeError(
        f"all Gemini keys/models exhausted during Anthropic fallback: {last_err}"
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
    except Exception as gemini_exc:
        # Both providers failed — log Gemini's reason so the user can see it,
        # then print the specific sentinel for the GUI dialog.
        print(
            f"[vp] warn: Gemini fallback also failed ({str(gemini_exc)[:120]})",
            flush=True,
        )
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
