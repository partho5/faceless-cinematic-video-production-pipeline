"""Anthropic call helper with OpenRouter free-tier fallback.

Primary provider: Anthropic. On ANY Anthropic failure the identical prompt
is retried via OpenRouter, rotating through all available OPENROUTER_API_KEY*
values and a cascade of free long-context models. If every provider also
fails, a [API_ERROR:…] sentinel is printed so the GUI can surface a
user-friendly dialog.

TTS continues to use Gemini exclusively — see pipeline/tts_gemini.py and
pipeline/voice.py. This module no longer touches Gemini.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def _collect_openrouter_keys() -> list[str]:
    """All usable OpenRouter keys: OPENROUTER_API_KEY + OPENROUTER_API_KEYS
    comma-list + OPENROUTER_API_KEY_1…24.  Mirrors the Gemini key collection
    pattern used by the TTS stage.

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

    _add(env.get("OPENROUTER_API_KEY", ""))
    for k in env.get("OPENROUTER_API_KEYS", "").split(","):
        _add(k)
    for i in range(1, 25):
        _add(env.get(f"OPENROUTER_API_KEY_{i}", ""))
    return out


_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Three free long-context models from three different upstream vendors.
# Uncorrelated outages: when DeepSeek's free endpoint is saturated, the
# Meta/Qwen pools usually aren't. Override the whole cascade with the
# OPENROUTER_LLM_MODEL env var (single model only).
_OPENROUTER_DEFAULT_MODELS = [
    "openai/gpt-oss-120b:free",
    "deepseek/deepseek-v4-flash:free",
    "meta-llama/llama-3.3-70b-instruct:free",
]


def _openrouter_text(system: str, user: str) -> tuple[str, str]:
    """OpenRouter free-tier text generation with key + model rotation.

    Tries every key on every model in the cascade before giving up.
    Per-key errors are recorded and skipped so that a bad key, rate-limit
    burst, or transient error on one key does not abort the entire
    fallback. Raises RuntimeError only when every key × model combination
    has failed. Returns (text, model_id) so the caller can record the call.
    """
    keys = _collect_openrouter_keys()
    if not keys:
        raise RuntimeError("no OpenRouter API keys available for fallback")

    override = os.environ.get("OPENROUTER_LLM_MODEL", "").strip()
    models = [override] if override else _OPENROUTER_DEFAULT_MODELS

    last_err: Exception | None = None
    for model in models:
        for key in keys:
            try:
                payload = json.dumps({
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": 4000,
                }).encode("utf-8")
                req = urllib.request.Request(
                    _OPENROUTER_URL,
                    method="POST",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                        # Optional analytics headers OpenRouter recommends;
                        # harmless if the receiver ignores them.
                        "HTTP-Referer": "https://github.com/anthropic/video-production",
                        "X-Title": "Video Production Studio",
                    },
                    data=payload,
                )
                with urllib.request.urlopen(req, timeout=180) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                text = body["choices"][0]["message"]["content"]
                if not text:
                    raise ValueError("empty response from OpenRouter")
                print(
                    f"[vp] OpenRouter fallback succeeded via {model}",
                    flush=True,
                )
                return text, model
            except Exception as e:
                last_err = e
                # urllib hides the HTTP response body in HTTPError; surface
                # it so users can see "rate-limited" vs "invalid key" etc.
                detail = str(e)
                if isinstance(e, urllib.error.HTTPError):
                    try:
                        detail = (
                            f"HTTP {e.code} "
                            f"{e.read().decode('utf-8', errors='replace')[:200]}"
                        )
                    except Exception:
                        detail = f"HTTP {e.code}"
                print(
                    f"[vp] OpenRouter key failed ({model}): {detail[:200]} "
                    f"— trying next…",
                    flush=True,
                )
                continue
    raise RuntimeError(
        f"all OpenRouter keys/models exhausted during Anthropic fallback: "
        f"{last_err}"
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

    # ---------------------------------------------------------------- OpenRouter fallback
    print(
        f"[vp] warn: Anthropic failed ({str(anthropic_exc)[:80]}) — retrying via OpenRouter…",
        flush=True,
    )
    try:
        text, or_model = _openrouter_text(system, user)
        from .cost import record
        record(or_model, None, getattr(spec, "purpose", "llm"))
        return text
    except Exception as openrouter_exc:
        # Both providers failed — log OpenRouter's reason so the user can see
        # it, then print the specific sentinel for the GUI dialog.
        print(
            f"[vp] warn: OpenRouter fallback also failed ({str(openrouter_exc)[:120]})",
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
