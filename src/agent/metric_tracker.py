"""src/agent/metric_tracker.py — cost + token tracking for judge calls.

Pricing model:
  _DEFAULT_PRICING: dict[model_name] -> {"in": $/1k tok, "out": $/1k tok}
  compute_cost(model, tok_in, tok_out) -> (cost_usd, warn: bool)
  Unknown models return $0.00 + warn=True (the eval runner logs the
  warning but does not fail).

MetricTracker:
  - record(email_id, model, tokens_in, tokens_out, duration_ms, ...)
    appends a per-call event.
  - per_email / run_totals / finalize() / write cost.json.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pricing registry
# ---------------------------------------------------------------------------
# Source: public list prices as of 2026-09; may be overridden by the user
# via JUDGE_PRICING_IN / JUDGE_PRICING_OUT env vars.
_DEFAULT_PRICING: dict[str, dict[str, float]] = {
    # --- OpenAI ---
    "gpt-4o-mini":                 {"in": 0.000150, "out": 0.000600},
    "gpt-4o":                       {"in": 0.002500, "out": 0.010000},
    "gpt-4.1":                      {"in": 0.002000, "out": 0.008000},
    "gpt-4.1-mini":                 {"in": 0.000400, "out": 0.001600},
    "gpt-4.1-nano":                 {"in": 0.000100, "out": 0.000400},
    "o4-mini":                      {"in": 0.001100, "out": 0.004400},
    # --- Anthropic ---
    "claude-3-5-sonnet-latest":     {"in": 0.003000, "out": 0.015000},
    "claude-3-5-haiku-latest":      {"in": 0.000800, "out": 0.004000},
    "claude-3-haiku-20240307":      {"in": 0.000250, "out": 0.001250},
    "claude-3-opus-20240229":       {"in": 0.015000, "out": 0.075000},
    # --- Mistral ---
    "mistral-large-latest":         {"in": 0.002000, "out": 0.006000},
    # --- Groq (Llama) ---
    "llama-3.1-70b-versatile":      {"in": 0.000590, "out": 0.000790},
    "llama-3.1-8b-instant":         {"in": 0.000050, "out": 0.000080},
    # --- Google ---
    "gemini-1.5-pro":               {"in": 0.001250, "out": 0.005000},
    "gemini-1.5-flash":             {"in": 0.000075, "out": 0.000300},
    # --- MiniMax (official API) ---
    # Source: MiniMax public pricing 2026-09 (CNY -> USD @ 7.2).
    # Base URL: https://api.minimaxi.com/v1   (OpenAI-compatible)
    # MiniMax-Text-01 + abab6.5s-chat share a 204k context window.
    "MiniMax-Text-01":              {"in": 0.000140, "out": 0.001110},
    "abab6.5s-chat":                {"in": 0.000140, "out": 0.001110},
    "abab6.5-chat":                 {"in": 0.000140, "out": 0.000140},
    "abab5.5s-chat":                {"in": 0.000690, "out": 0.000690},
    "abab5.5-chat":                 {"in": 0.002080, "out": 0.002080},
    # --- OpenRouter (model-passthrough; OpenAI-compatible) ---
    # Free-tier models (suffix ":free") cost $0.00; the routing layer
    # charges through the upstream provider. Override with
    # JUDGE_PRICING_IN / JUDGE_PRICING_OUT for non-free models.
    "minimax/minimax-m3:free":      {"in": 0.000000, "out": 0.000000},
    "meta-llama/llama-3.3-70b-instruct:free": {"in": 0.000000, "out": 0.000000},
    "google/gemini-2.0-flash-exp:free":       {"in": 0.000000, "out": 0.000000},
    "qwen/qwen-2.5-72b-instruct:free":        {"in": 0.000000, "out": 0.000000},
}


def _env_rates() -> Optional[dict[str, float]]:
    """Read override rates from env (JUDGE_PRICING_IN / JUDGE_PRICING_OUT)."""
    pin = os.getenv("JUDGE_PRICING_IN")
    pout = os.getenv("JUDGE_PRICING_OUT")
    if pin is None or pout is None:
        return None
    try:
        return {"in": float(pin), "out": float(pout)}
    except ValueError:
        return None


def compute_cost(model: str, tokens_in: int, tokens_out: int) -> tuple[float, bool]:
    """Return (cost_usd, warn_flag). cost_usd is $ / 1k-token-based."""
    rates = _env_rates() or _DEFAULT_PRICING.get(model)
    warn = rates is None
    if rates is None:
        return 0.0, True
    cost = (tokens_in / 1000.0) * rates["in"] + (tokens_out / 1000.0) * rates["out"]
    return cost, False


def list_known_models() -> list[str]:
    """Return the sorted list of model names with bundled pricing."""
    return sorted(_DEFAULT_PRICING.keys())


def has_pricing(model: str) -> bool:
    """True if the model has a bundled rate; env override always counts."""
    return _env_rates() is not None or model in _DEFAULT_PRICING


def detect_provider(model: str, base_url: Optional[str] = None) -> str:
    """Best-effort provider detection from model name + base_url.

    Returns a short tag like 'minimax', 'openai', 'anthropic', 'google',
    'groq', 'mistral', 'local', 'unknown'. Used for log lines and
    cost-report aggregation.
    """
    if base_url:
        u = base_url.lower()
        if "minimaxi.com" in u or "MiniMax" in u or "minimax" in u:
            return "minimax"
        if "openrouter.ai" in u:
            return "openrouter"
        if "openai.com" in u:
            return "openai"
        if "anthropic.com" in u:
            return "anthropic"
        if "googleapis.com" in u or "generativelanguage" in u:
            return "google"
        if "groq.com" in u:
            return "groq"
        if "mistral" in u:
            return "mistral"
        if "localhost" in u or "127.0.0.1" in u or "vllm" in u:
            return "local"
    m = (model or "").lower()
    if m.startswith("MiniMax") or m.startswith("abab"):
        return "minimax"
    if "/" in m and m.split("/", 1)[0] in {"openai", "anthropic", "google",
                                            "meta-llama", "mistralai",
                                            "qwen", "minimax", "cohere",
                                            "deepseek", "x-ai", "perplexity",
                                            "nousresearch", "microsoft"}:
        # OpenRouter-style `vendor/model` ID; the vendor is the first segment.
        # We don't know the upstream provider from the slug alone; mark as openrouter
        # when the base URL points there, otherwise the caller can override.
        return "openrouter" if (base_url and "openrouter" in base_url.lower()) else "unknown"
    if m.startswith("gpt-") or m.startswith("o4-") or m.startswith("o3-"):
        return "openai"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("gemini"):
        return "google"
    if m.startswith("llama-"):
        return "groq"
    if m.startswith("mistral"):
        return "mistral"
    return "unknown"


# ---------------------------------------------------------------------------
# MetricTracker
# ---------------------------------------------------------------------------
@dataclass
class _Call:
    email_id:    str
    model:       str
    tokens_in:   int
    tokens_out:  int
    duration_ms: int
    cost_usd:    float
    ts:          str
    event:       str = "judge_call"
    extra:       dict = field(default_factory=dict)


class MetricTracker:
    """Collect per-call events; emit per-email + per-run aggregates; write cost.json.

    Usage:
        mt = MetricTracker(out_path="data/runs/cost.json")
        mt.record(email_id="E1", model="gpt-4o-mini",
                  tokens_in=100, tokens_out=50, duration_ms=200)
        ...
        mt.finalize()
    """

    SCHEMA_VERSION = "1.0"

    def __init__(self, out_path: Path | str | None = None) -> None:
        self.out_path: Optional[Path] = Path(out_path) if out_path else None
        self._calls: list[_Call] = []
        self._started_at = datetime.now(timezone.utc).isoformat()
        self.per_email: dict[str, dict] = {}
        self.run_totals: dict = {
            "calls": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_usd": 0.0,
            "duration_ms": 0,
        }

    # ----- public API -----
    def record(
        self,
        *,
        email_id: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        duration_ms: int,
        event: str = "judge_call",
        **extra,
    ) -> None:
        cost, warn = compute_cost(model, tokens_in, tokens_out)
        if warn:
            log.warning(
                "no pricing for model %r; recording cost_usd=0.0. "
                "Set JUDGE_PRICING_IN / JUDGE_PRICING_OUT to override.",
                model,
            )
        call = _Call(
            email_id=email_id, model=model,
            tokens_in=tokens_in, tokens_out=tokens_out,
            duration_ms=duration_ms, cost_usd=cost,
            ts=datetime.now(timezone.utc).isoformat(),
            event=event, extra=dict(extra),
        )
        self._calls.append(call)

        # per-email aggregate
        agg = self.per_email.setdefault(email_id, {
            "calls": 0, "tokens_in": 0, "tokens_out": 0,
            "cost_usd": 0.0, "duration_ms": 0,
        })
        agg["calls"]        += 1
        agg["tokens_in"]    += tokens_in
        agg["tokens_out"]   += tokens_out
        agg["cost_usd"]     += cost
        agg["duration_ms"]  += duration_ms

        # run totals
        self.run_totals["calls"]        += 1
        self.run_totals["tokens_in"]    += tokens_in
        self.run_totals["tokens_out"]   += tokens_out
        self.run_totals["cost_usd"]     += cost
        self.run_totals["duration_ms"]  += duration_ms

    def finalize(self) -> None:
        if self.out_path is None:
            return
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "started_at":     self._started_at,
            "finished_at":    datetime.now(timezone.utc).isoformat(),
            "calls":          [self._serialize(c) for c in self._calls],
            "per_email":      self.per_email,
            "totals":         self.run_totals,
        }
        self.out_path.write_text(json.dumps(payload, default=str, indent=2))

    # ----- internals -----
    @staticmethod
    def _serialize(c: _Call) -> dict:
        d = {
            "event":       c.event,
            "email_id":    c.email_id,
            "model":       c.model,
            "tokens_in":   c.tokens_in,
            "tokens_out":  c.tokens_out,
            "duration_ms": c.duration_ms,
            "cost_usd":    round(c.cost_usd, 6),
            "ts":          c.ts,
        }
        if c.extra:
            d.update(c.extra)
        return d
