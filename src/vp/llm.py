"""Anthropic call helper.

Newer models (e.g. claude-opus-4-7) reject the now-deprecated `temperature`
param with a 400. We attempt the call as configured and, on that specific
error, transparently retry without the offending param — so config can keep
declaring temperature for models that still accept it without breaking ones
that don't.
"""
from __future__ import annotations


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

    try:
        msg = client.messages.create(**kwargs)
    except anthropic.BadRequestError as e:
        if "temperature" in str(e).lower() and "temperature" in kwargs:
            kwargs.pop("temperature")
            msg = client.messages.create(**kwargs)
        else:
            raise

    # plug-and-play cost tracking (best-effort; tagged by config purpose)
    from .cost import record
    record(spec.model, getattr(msg, "usage", None),
           getattr(spec, "purpose", "llm"))

    return "".join(b.text for b in msg.content if b.type == "text")


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
