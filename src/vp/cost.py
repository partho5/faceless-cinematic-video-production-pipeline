"""Plug-and-play LLM cost tracker.

One process-wide singleton accumulates token usage for every Anthropic call
(recorded centrally from llm.anthropic_message, so no call site changes), then
run.py dumps a per-run report: total USD, per-model + per-stage breakdown, a
one-line context summary, and the video title/slug/path.

Pricing is USD per 1,000,000 tokens (Anthropic public list pricing, standard
context). Cache write = 1.25x base input, cache read = 0.10x base input.
Prices change — they live in ONE table below; edit there, nothing else.
Recording is best-effort and MUST never raise into the pipeline.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

# $ per 1M tokens: (input, output). Keys match config.yaml model ids.
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-7": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    # OpenRouter free-tier fallback models — $0 cost
    "openai/gpt-oss-120b:free": (0.0, 0.0),
    "deepseek/deepseek-v4-flash:free": (0.0, 0.0),
    "meta-llama/llama-3.3-70b-instruct:free": (0.0, 0.0),
    # Groq fallback models — $0 cost
    "qwen/qwen3-32b": (0.0, 0.0),
    # OpenAI fallback model (public list pricing)
    "gpt-4o-mini": (0.15, 0.60),
}
# fallback for an unknown/new model id (Sonnet-class), flagged in the report.
_FALLBACK = (3.0, 15.0)
_MILLION = 1_000_000.0


def _rates(model: str) -> tuple[tuple[float, float], bool]:
    if model in _PRICING:
        return _PRICING[model], True
    return _FALLBACK, False


@dataclass
class _Entry:
    purpose: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_write: int
    cache_read: int
    cost_usd: float
    known_price: bool


@dataclass
class CostTracker:
    title: str = ""
    slug: str = ""
    path: str = ""
    started: float = field(default_factory=time.time)
    entries: list[_Entry] = field(default_factory=list)

    # -- lifecycle ---------------------------------------------------------
    def start(self, *, title: str, slug: str, path: str) -> None:
        """Begin a fresh run (clears prior accumulation)."""
        self.title, self.slug, self.path = title, slug, str(path)
        self.started = time.time()
        self.entries.clear()

    # -- recording (best-effort; never raises) -----------------------------
    def record(self, model: str, usage, purpose: str = "llm") -> None:
        try:
            it = int(getattr(usage, "input_tokens", 0) or 0)
            ot = int(getattr(usage, "output_tokens", 0) or 0)
            cw = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
            cr = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            (pin, pout), known = _rates(model)
            cost = (
                (it + cw * 1.25 + cr * 0.10) * pin + ot * pout
            ) / _MILLION
            self.entries.append(_Entry(
                purpose=purpose, model=model, input_tokens=it,
                output_tokens=ot, cache_write=cw, cache_read=cr,
                cost_usd=round(cost, 6), known_price=known))
        except Exception:
            pass  # cost tracking must never break a render

    # -- summary -----------------------------------------------------------
    def total(self) -> float:
        return round(sum(e.cost_usd for e in self.entries), 4)

    def _agg(self, key) -> dict:
        out: dict[str, dict] = {}
        for e in self.entries:
            b = out.setdefault(key(e), {"calls": 0, "input": 0, "output": 0,
                                        "cost_usd": 0.0})
            b["calls"] += 1
            b["input"] += e.input_tokens + e.cache_write + e.cache_read
            b["output"] += e.output_tokens
            b["cost_usd"] = round(b["cost_usd"] + e.cost_usd, 6)
        return out

    def context_line(self) -> str:
        n = len(self.entries)
        models = sorted({e.model for e in self.entries}) or ["(no llm calls)"]
        approx = "" if all(e.known_price for e in self.entries) else \
            " (approx: unpriced model)"
        return (f'"{self.title or self.slug}" — {n} LLM call(s) on '
                f'{", ".join(models)} = ${self.total():.4f}{approx}')

    def summary(self) -> dict:
        return {
            "video_title": self.title,
            "video_slug": self.slug,
            "video_path": self.path,
            "context": self.context_line(),
            "total_cost_usd": self.total(),
            "llm_calls": len(self.entries),
            "duration_s": round(time.time() - self.started, 1),
            "by_model": self._agg(lambda e: e.model),
            "by_stage": self._agg(lambda e: e.purpose),
        }

    # -- persistence -------------------------------------------------------
    def save(self, out_dir: Path, ledger: Path | None = None) -> dict:
        """Write per-run llm_cost.json (+ append a global JSONL ledger)."""
        s = self.summary()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "llm_cost.json").write_text(
                json.dumps(s, indent=2), encoding="utf-8")
            if ledger is not None:
                ledger.parent.mkdir(parents=True, exist_ok=True)
                with ledger.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "ts": int(self.started),
                        "title": s["video_title"],
                        "slug": s["video_slug"],
                        "path": s["video_path"],
                        "total_cost_usd": s["total_cost_usd"],
                        "context": s["context"],
                    }) + "\n")
        except Exception:
            pass
        return s


# process-wide singleton
TRACKER = CostTracker()


def record(model: str, usage, purpose: str = "llm") -> None:
    """Module-level shim used by llm.anthropic_message (one-liner hook)."""
    TRACKER.record(model, usage, purpose)
