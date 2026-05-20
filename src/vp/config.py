"""Central config + env loader.

Single source of truth for: project paths, `.env` secrets, and the
per-purpose model table from `config.yaml` (planning/10). No model IDs are
hardcoded anywhere else — modules ask this module for their purpose's model.

Offline mode: if a purpose's API key env var is missing/empty, that purpose
is marked `offline` and its module falls back to a deterministic local stub
so the whole pipeline is still runnable end-to-end without keys.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------- paths -----
ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "assets"
OUTPUT = ROOT / "output"
CACHE = ROOT / ".cache"
CLIP_CACHE = ASSETS / "clips_cache"


@dataclass(frozen=True)
class ModelSpec:
    """Resolved model for one purpose. `offline` => use the local stub."""

    purpose: str
    provider: str
    model: str
    params: dict[str, Any] = field(default_factory=dict)
    api_key_env: str | None = None
    api_key: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def offline(self) -> bool:
        # local engines never need a key; remote ones need a non-empty key.
        if self.provider in ("local", "frame_compose") or self.provider is None:
            return False
        return not self.api_key


def _parse_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class Config:
    """Loaded config + secrets. Build with `Config.load()`."""

    def __init__(self, raw: dict[str, Any], env: dict[str, str]):
        self._raw = raw
        self._env = env
        self._models: dict[str, ModelSpec] = {}
        self._resolve_models()

    # -- construction --------------------------------------------------------
    @classmethod
    def load(cls, config_path: Path | None = None, env_path: Path | None = None) -> "Config":
        cfg_path = config_path or (ROOT / "config.yaml")
        if not cfg_path.exists():
            cfg_path = ROOT / "config.example.yaml"  # accept defaults
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

        env = dict(os.environ)
        env.update(_parse_env_file(env_path or (ROOT / ".env")))
        return cls(raw, env)

    # purposes whose model is overridable via the VP_LLM_MODEL env var
    # (dev cost lever: e.g. claude-haiku-4-5-20251001). Prod = config.yaml
    # default (claude-sonnet-4-6); leave VP_LLM_MODEL unset in prod.
    _LLM_OVERRIDE_PURPOSES = ("script_generation", "segmentation_direction",
                              "sound_design", "music_design")
    _LLM_OVERRIDE_ENV = "VP_LLM_MODEL"

    # -- model resolution ----------------------------------------------------
    def _resolve_models(self) -> None:
        models = self._raw.get("models")
        if not models:
            raise ConfigError("config.yaml has no `models:` section")

        llm_override = self._env.get(self._LLM_OVERRIDE_ENV, "").strip()

        for purpose, spec in models.items():
            provider = spec.get("provider") or spec.get("mode") or "local"
            key_env = spec.get("api_key_env")
            api_key = self._env.get(key_env, "").strip() if key_env else None
            model_id = spec.get("model") or spec.get("engine") or "(local)"
            if llm_override and purpose in self._LLM_OVERRIDE_PURPOSES:
                model_id = llm_override
            extra = {
                k: v
                for k, v in spec.items()
                if k not in {"provider", "model", "params", "api_key_env", "mode", "engine"}
            }
            self._models[purpose] = ModelSpec(
                purpose=purpose,
                provider=provider,
                model=model_id,
                params=spec.get("params", {}) or {},
                api_key_env=key_env,
                api_key=api_key or None,
                extra=extra,
            )

    def model(self, purpose: str) -> ModelSpec:
        if purpose not in self._models:
            raise ConfigError(
                f"purpose '{purpose}' not in config.yaml models "
                f"(have: {sorted(self._models)})"
            )
        return self._models[purpose]

    @property
    def models(self) -> dict[str, ModelSpec]:
        return dict(self._models)

    def env(self, key: str) -> str:
        return self._env.get(key, "").strip()

    @property
    def app_name(self) -> str:
        return self._env.get("APP_NAME", "").strip() or "Video Production Studio"

    def _rotation_keys_present(self, m: ModelSpec) -> bool:
        """True if a purpose has usable ROTATION keys beyond its primary env.

        Mirrors voice.collect_keys (primary + comma-list + NUMBERED _1..)
        without importing it (voice imports config). Used so the offline
        check / warning is not fooled by an empty primary when a rotation
        pool is configured (e.g. only GEMINI_API_KEY_1.. set).
        """
        rot_env = m.extra.get("api_key_rotation_env")
        if rot_env and any(s.strip()
                           for s in self.env(rot_env).split(",")):
            return True
        base = m.api_key_env or ""
        if base:
            for i in range(1, 25):                 # _1.._24, gap-tolerant
                if self.env(f"{base}_{i}"):
                    return True
        return False

    def effective_offline(self, purpose: str) -> bool:
        """offline() but rotation-aware: a configured rotation pool keeps a
        purpose ONLINE even when its primary api_key_env is empty."""
        m = self.model(purpose)
        if not m.offline:
            return False
        return not self._rotation_keys_present(m)

    # -- startup validation --------------------------------------------------
    REQUIRED_PURPOSES = (
        "script_generation",
        "segmentation_direction",
        "metadata_text",
        "thumbnail_image",
        "tts_audio",
        "forced_alignment",
    )

    def validate(self) -> list[str]:
        """Fail fast on structural or credential errors; return non-fatal warnings."""
        missing = [p for p in self.REQUIRED_PURPOSES if p not in self._models]
        if missing:
            raise ConfigError(f"config.yaml missing required purposes: {missing}")

        for purpose, m in self._models.items():
            if m.api_key_env and self.effective_offline(purpose):
                print(
                    f"[vp] [API_ERROR:KEY_NOT_SET] {m.api_key_env} not configured"
                    f" (required for '{purpose}')",
                    flush=True,
                )
                raise ConfigError(
                    f"Missing API key for '{purpose}': "
                    f"{m.api_key_env} (or rotation keys) not set — "
                    f"set the key in .env or environment and retry."
                )
        for d in (ASSETS, OUTPUT, CACHE, CLIP_CACHE):
            d.mkdir(parents=True, exist_ok=True)
        return []

    def resolved_models_manifest(self) -> dict[str, dict[str, Any]]:
        """Per-purpose resolved model snapshot for render_manifest.json (G14)."""
        return {
            p: {"provider": m.provider, "model": m.model,
                "offline": self.effective_offline(p)}
            for p, m in self._models.items()
        }


class ConfigError(RuntimeError):
    pass


_CONFIG: Config | None = None


def get_config() -> Config:
    """Process-wide singleton."""
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = Config.load()
    return _CONFIG
