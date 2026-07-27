"""Anthropic call helper with Groq / OpenAI / OpenRouter fallback chain.

Primary provider: Anthropic (Claude). On ANY Anthropic failure — or if
ANTHROPIC_API_KEY is absent — the identical prompt is retried, in order,
via: Groq, then OpenAI (gpt-4o-mini), then OpenRouter's free-tier model
cascade. A provider whose API key is missing/blank/commented in .env is
skipped without ever attempting a network call — it's treated the same as
a failure and the chain moves on. If every provider fails, a [API_ERROR:…]
sentinel is printed so the GUI can surface a user-friendly dialog.

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


class GroqUsageWrapper:
    def __init__(self, usage):
        self.input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        self.output_tokens = getattr(usage, "completion_tokens", 0) or 0


def _groq_text(spec, system: str, user: str) -> tuple[str, str, Any]:
    """Groq API fallback text generation.
    Returns (text, model_name, usage_object).
    """
    from groq import Groq
    from .config import get_config

    try:
        cfg = get_config()
        groq_api_key = cfg.env("GROQ_API_KEY")
    except Exception:
        from .config import ROOT, _parse_env_file
        env = {**os.environ}
        env.update(_parse_env_file(ROOT / ".env"))
        groq_api_key = env.get("GROQ_API_KEY", "")

    if not groq_api_key:
        raise RuntimeError("GROQ_API_KEY not configured")

    client = Groq(api_key=groq_api_key)
    model = os.environ.get("GROQ_LLM_MODEL", "qwen/qwen3-32b").strip()

    params = dict(spec.params or {})
    temperature = params.get("temperature", 0.6)
    # Cap max_tokens for Groq because its TPM rate-limit checks against max_completion_tokens.
    # Set to 1000 so that sequential attempts can fit within Groq's 6,000 TPM free limit.
    max_tokens = min(params.get("max_tokens", 4096), 1000)

    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_completion_tokens": max_tokens,
        "top_p": 0.95,
        "reasoning_effort": "none",
        "stream": True,
        "stop": None,
        "stream_options": {"include_usage": True},
    }

    completion = None
    try:
        try:
            completion = client.chat.completions.create(**kwargs)
        except Exception as e:
            # If reasoning_effort or stream_options causes issues, try stripping them
            err_msg = str(e).lower()
            changed = False
            if "stream_options" in err_msg and "stream_options" in kwargs:
                kwargs.pop("stream_options")
                changed = True
            if "reasoning_effort" in err_msg and "reasoning_effort" in kwargs:
                kwargs.pop("reasoning_effort")
                changed = True
            if changed:
                completion = client.chat.completions.create(**kwargs)
            else:
                raise
    except Exception:
        # Fallback to absolute minimal standard arguments
        kwargs_minimal = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
            "stream": True,
        }
        completion = client.chat.completions.create(**kwargs_minimal)

    chunks = []
    usage = None
    for chunk in completion:
        if hasattr(chunk, "usage") and chunk.usage is not None:
            usage = chunk.usage
        if chunk.choices and len(chunk.choices) > 0:
            content = chunk.choices[0].delta.content or ""
            if content:
                chunks.append(content)

    text = "".join(chunks)
    if not text:
        raise ValueError("empty response from Groq")

    return text, model, usage


class OpenAIUsageWrapper:
    def __init__(self, usage):
        self.input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        self.output_tokens = getattr(usage, "completion_tokens", 0) or 0


def _openai_text(spec, system: str, user: str) -> tuple[str, str, Any]:
    """OpenAI fallback text generation (default model: gpt-4o-mini).
    Returns (text, model_name, usage_object).

    Mirrors _groq_text: reads the key from Config first, falls back to a
    direct .env parse (Config loads .env into its own dict, not os.environ).
    Model is overridable via OPENAI_MODEL (matches the user's own .env var
    naming); default is gpt-4o-mini.
    """
    from openai import OpenAI
    from .config import get_config

    try:
        cfg = get_config()
        openai_api_key = cfg.env("OPENAI_API_KEY")
    except Exception:
        from .config import ROOT, _parse_env_file
        env = {**os.environ}
        env.update(_parse_env_file(ROOT / ".env"))
        openai_api_key = env.get("OPENAI_API_KEY", "")

    if not openai_api_key:
        raise RuntimeError("OPENAI_API_KEY not configured")

    client = OpenAI(api_key=openai_api_key)
    model = os.environ.get("OPENAI_MODEL", "").strip() or "gpt-4o-mini"

    params = dict(spec.params or {})
    temperature = params.get("temperature", 0.6)
    max_tokens = params.get("max_tokens", 4000)

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    text = completion.choices[0].message.content or ""
    if not text:
        raise ValueError("empty response from OpenAI")

    return text, model, getattr(completion, "usage", None)


def _clean_think(text: str) -> str:
    import re
    if "<think>" in text:
        if "</think>" in text:
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        else:
            text = text.split("<think>")[0]
    return text.strip()


def _key_present(name: str) -> bool:
    """Cheap presence check for a named .env/environment key (no network).

    Used only to pick which provider's exception is the "root cause" when
    every fallback tier has been exhausted — never to gate an actual call
    (each tier already gates its own call independently).
    """
    try:
        from .config import get_config
        if get_config().env(name):
            return True
    except Exception:
        pass
    try:
        from .config import ROOT, _parse_env_file
        env = {**os.environ}
        env.update(_parse_env_file(ROOT / ".env"))
        return bool(env.get(name, "").strip())
    except Exception:
        return False


def anthropic_message(spec, *, system: str, user: str) -> str:
    anthropic_exc: Exception | None = None
    msg = None

    if spec.api_key:
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
            raw_text = "".join(b.text for b in msg.content if b.type == "text")
            return _clean_think(raw_text)
    else:
        # Simulate Anthropic exception since it's not set
        anthropic_exc = RuntimeError("Anthropic API key is missing (not set in .env)")

    # ---------------------------------------------------------------- Groq fallback
    groq_exc: Exception | None = None
    try:
        from .config import get_config
        try:
            cfg = get_config()
            groq_key = cfg.env("GROQ_API_KEY")
        except Exception:
            from .config import ROOT, _parse_env_file
            env = {**os.environ}
            env.update(_parse_env_file(ROOT / ".env"))
            groq_key = env.get("GROQ_API_KEY", "")

        if not groq_key:
            raise RuntimeError("GROQ_API_KEY not configured")

        if spec.api_key:
            print(
                f"[vp] warn: Anthropic failed ({str(anthropic_exc)[:80]}) — retrying via Groq…",
                flush=True,
            )
        else:
            print(
                f"[vp] info: Anthropic not configured — attempting via Groq…",
                flush=True,
            )

        text, groq_model, usage = _groq_text(spec, system, user)
        from .cost import record
        usage_obj = GroqUsageWrapper(usage) if usage is not None else None
        record(groq_model, usage_obj, getattr(spec, "purpose", "llm"))
        return _clean_think(text)
    except Exception as e:
        groq_exc = e

    # ---------------------------------------------------------------- OpenAI fallback
    openai_exc: Exception | None = None
    try:
        if spec.api_key:
            print(
                f"[vp] warn: Anthropic failed ({str(anthropic_exc)[:50]}) and "
                f"Groq failed ({str(groq_exc)[:50]}) — retrying via OpenAI…",
                flush=True,
            )
        else:
            print(
                f"[vp] info: Anthropic not configured and Groq failed "
                f"({str(groq_exc)[:60]}) — attempting via OpenAI…",
                flush=True,
            )

        text, openai_model, usage = _openai_text(spec, system, user)
        from .cost import record
        usage_obj = OpenAIUsageWrapper(usage) if usage is not None else None
        record(openai_model, usage_obj, getattr(spec, "purpose", "llm"))
        return _clean_think(text)
    except Exception as e:
        openai_exc = e

    # ---------------------------------------------------------------- OpenRouter fallback
    if spec.api_key:
        err_origin = (
            f"Anthropic failed ({str(anthropic_exc)[:40]}), "
            f"Groq failed ({str(groq_exc)[:40]}) and "
            f"OpenAI failed ({str(openai_exc)[:40]})"
        )
    else:
        err_origin = (
            f"Groq failed ({str(groq_exc)[:50]}) and "
            f"OpenAI failed ({str(openai_exc)[:50]})"
        )

    print(
        f"[vp] warn: {err_origin} — retrying via OpenRouter…",
        flush=True,
    )
    try:
        text, or_model = _openrouter_text(system, user)
        from .cost import record
        record(or_model, None, getattr(spec, "purpose", "llm"))
        return _clean_think(text)
    except Exception as openrouter_exc:
        # All providers failed — log reasons so the user can see
        # then print the specific sentinel for the GUI dialog.
        print(
            f"[vp] warn: OpenRouter fallback also failed ({str(openrouter_exc)[:120]})",
            flush=True,
        )

        # Root-cause exception = whichever provider was actually the
        # intended primary: Anthropic if configured, else the first of
        # Groq/OpenAI that had a key configured, else Groq's
        # "not configured" message as a last resort (nothing was set up).
        if spec.api_key:
            primary_exc = anthropic_exc
        elif _key_present("GROQ_API_KEY"):
            primary_exc = groq_exc
        elif _key_present("OPENAI_API_KEY"):
            primary_exc = openai_exc
        else:
            primary_exc = groq_exc
        _err = str(primary_exc)
        _err_lower = _err.lower()

        import anthropic
        if isinstance(primary_exc, anthropic.AuthenticationError):
            print(
                f"[vp] [API_ERROR:ANTHROPIC_KEY_INVALID] {_err[:200]}",
                flush=True,
            )
        elif isinstance(primary_exc, anthropic.PermissionDeniedError):
            etype = (
                "ANTHROPIC_CREDITS"
                if any(k in _err_lower
                       for k in ("credit", "billing", "balance", "payment"))
                else "ANTHROPIC_KEY_INVALID"
            )
            print(f"[vp] [API_ERROR:{etype}] {_err[:200]}", flush=True)
        elif isinstance(primary_exc, anthropic.BadRequestError):
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
        elif not spec.api_key and primary_exc is openai_exc:
            try:
                import openai as openai_sdk
                is_auth_error = isinstance(primary_exc, openai_sdk.AuthenticationError)
                is_rate_error = isinstance(
                    primary_exc,
                    (openai_sdk.RateLimitError, openai_sdk.PermissionDeniedError),
                )
            except ImportError:
                is_auth_error = "authentication" in _err_lower or "api_key" in _err_lower or "unauthorized" in _err_lower
                is_rate_error = "rate_limit" in _err_lower or "quota" in _err_lower or "insufficient_quota" in _err_lower

            if is_auth_error:
                print(
                    f"[vp] [API_ERROR:OPENAI_KEY_INVALID] {_err[:200]}",
                    flush=True,
                )
            elif is_rate_error:
                print(
                    f"[vp] [API_ERROR:OPENAI_CREDITS] {_err[:200]}",
                    flush=True,
                )
            else:
                print(
                    f"[vp] [API_ERROR:LLM_ALL_FAILED] {_err[:200]}",
                    flush=True,
                )
        elif not spec.api_key:
            try:
                import groq
                is_auth_error = isinstance(primary_exc, groq.AuthenticationError)
                is_rate_error = isinstance(primary_exc, (groq.RateLimitError, groq.PermissionDeniedError))
            except ImportError:
                is_auth_error = "authentication" in _err_lower or "api_key" in _err_lower or "unauthorized" in _err_lower
                is_rate_error = "rate_limit" in _err_lower or "quota" in _err_lower or "credits" in _err_lower

            if is_auth_error:
                print(
                    f"[vp] [API_ERROR:GROQ_KEY_INVALID] {_err[:200]}",
                    flush=True,
                )
            elif is_rate_error:
                print(
                    f"[vp] [API_ERROR:GROQ_CREDITS] {_err[:200]}",
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
        raise primary_exc


def tts_framing(spec, topic: str) -> tuple[str, str] | None:
    """Best-effort: derive niche-matched FIVE-FIELD voice direction from the
    topic, returned as a (scene, context) pair so VoiceStage consumes it
    unchanged.

    The LLM produces five orthogonal dimensions — scene, register, tone,
    pacing, inflection — that pack into the two-string API the rest of the
    pipeline expects. Gemini TTS responds to explicit layered direction far
    better than a single mood blob: a sourdough video sounds like a warm
    kitchen voice and a heist breakdown sounds like a controlled narrator
    without any per-video hand-tuning.

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
                "STRICT JSON and nothing else with these FIVE fields, each "
                "ONE short sentence (8–20 words). Every field must match "
                "the topic — a bedtime story sounds nothing like a heist "
                "breakdown, and a cooking video sounds nothing like a "
                "psychology explainer:\n"
                "{\n"
                '  "scene":      "<the imagined speaking moment: where the '
                "narrator is, who they're addressing>\",\n"
                '  "register":   "<intimacy + authority: whisper-close, '
                "friend-on-couch, lecturer, fireside chat, etc.>\",\n"
                '  "tone":       "<dominant emotional color: warm, knowing, '
                "conspiratorial, anxious, awed, patient, deadpan, etc.>\",\n"
                '  "pacing":     "<speed + pause discipline: unhurried with '
                "deliberate pauses, clipped and rising, measured and even, "
                "etc.>\",\n"
                '  "inflection": "<where the voice rises and falls: lift on '
                "reveals, drop on landings, flat for clinical beats, etc.>\"\n"
                "}\n"
                "Never default to thriller / psychology voice unless the "
                "topic itself is one of those. No prose outside the JSON."
            ),
            user=f"TOPIC: {topic}",
        )
        blob = raw[raw.find("{"): raw.rfind("}") + 1]
        d = json.loads(blob)
        scene = str(d.get("scene", "")).strip()
        register = str(d.get("register", "")).strip()
        tone = str(d.get("tone", "")).strip()
        pacing = str(d.get("pacing", "")).strip()
        inflection = str(d.get("inflection", "")).strip()
        if not (scene and register and tone and pacing and inflection):
            return None
        context = (
            f"Register: {register} Tone: {tone} "
            f"Pacing: {pacing} Inflection: {inflection}"
        )
        return (scene, context)
    except Exception:
        return None
