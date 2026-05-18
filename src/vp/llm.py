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
    return "".join(b.text for b in msg.content if b.type == "text")
