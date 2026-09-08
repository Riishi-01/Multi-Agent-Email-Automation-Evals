"""src/agent/judge.py — provider-agnostic OpenAI-compatible judge client.

Configuration (env-driven, lazy):
  JUDGE_BASE_URL  — e.g. https://api.openai.com/v1
  JUDGE_MODEL     — e.g. gpt-4o-mini, claude-3-5-sonnet-latest
  JUDGE_API_KEY   — bearer token

If any of these are unset, JudgeClient.is_configured() is False and
.judge() returns an UnscoredResult without hitting the network.

Transport seam:
  JudgeClient(..., transport=callable)
  transport(method, url, headers, data, timeout) -> (status, headers, body)
  Defaults to a urllib.request-based transport that hits JUDGE_BASE_URL.

Retry policy:
  Retries on 429 and 5xx (default max_retries=3, backoff=0.5s, exponential).
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass
class JudgeResult:
    score:       int
    rationale:   str
    tokens_in:   int
    tokens_out:  int
    duration_ms: int = 0
    model:       str = ""
    raw:         dict = field(default_factory=dict)
    unscored:    bool = False


@dataclass
class UnscoredResult:
    unscored:    bool = True
    score:       int = 0
    rationale:   str = ""
    tokens_in:   int = 0
    tokens_out:  int = 0
    duration_ms: int = 0
    model:       str = ""
    reason:      str = "judge not configured"


# ---------------------------------------------------------------------------
# Default urllib transport
# ---------------------------------------------------------------------------
def _default_transport(method: str, url: str, headers: dict,
                        data: bytes, timeout: float):
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() or b""
    except Exception as e:                                # pragma: no cover
        return 0, {}, str(e).encode("utf-8")


# ---------------------------------------------------------------------------
# JudgeClient
# ---------------------------------------------------------------------------
Transport = Callable[[str, str, dict, bytes, float], tuple]


class JudgeClient:
    """OpenAI-compatible chat client for the judge layer.

    Judge metrics (tone_professional, clarity_structure, completeness,
    matches_register, etc.) are Likert 1-5. The client sends the judge
    prompt and parses `{"score": int, "rationale": str}` out of the
    response. The score is clamped to [1, 5].
    """

    LIKERT_MIN = 1
    LIKERT_MAX = 5
    DEFAULT_TIMEOUT = 30.0
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_BACKOFF = 0.5

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        *,
        transport: Optional[Transport] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff: float = DEFAULT_BACKOFF,
    ) -> None:
        self.base_url = (base_url or os.getenv("JUDGE_BASE_URL") or "").rstrip("/")
        self.model    = model    or os.getenv("JUDGE_MODEL", "")
        self.api_key  = api_key  or os.getenv("JUDGE_API_KEY", "")
        self._transport: Transport = transport or _default_transport
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = retry_backoff

    # ----- config -----
    def is_configured(self) -> bool:
        return bool(self.base_url) and bool(self.model) and bool(self.api_key)

    def describe(self) -> dict:
        """Return a small dict describing the configured judge (safe to log)."""
        return {
            "base_url":   self.base_url,
            "model":      self.model,
            "api_key_set": bool(self.api_key),
            "timeout":    self._timeout,
            "max_retries": self._max_retries,
        }

    # ----- public -----
    def judge(self, *, system: str, user: str,
              metric_id: str = "") -> Any:
        """Send one judge call. Returns JudgeResult on success, UnscoredResult on failure."""
        if not self.is_configured():
            return UnscoredResult(reason="judge not configured")

        url = f"{self.base_url}/chat/completions"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        data = json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        t0 = time.perf_counter()
        last_err = ""
        for attempt in range(self._max_retries + 1):
            try:
                status, _h, raw = self._transport("POST", url, headers, data, self._timeout)
            except Exception as exc:                                # pragma: no cover
                last_err = f"{type(exc).__name__}: {exc}"
                self._sleep_backoff(attempt)
                continue

            if status == 200:
                return self._parse(raw, t0, metric_id)

            if status in (429, 500, 502, 503, 504):
                last_err = f"HTTP {status}"
                self._sleep_backoff(attempt)
                continue

            last_err = f"HTTP {status}: {raw[:200]!r}"
            break

        duration_ms = int((time.perf_counter() - t0) * 1000)
        log.warning("judge call failed (metric=%s, model=%s, err=%s)",
                    metric_id, self.model, last_err)
        return UnscoredResult(
            reason=f"judge call failed after retries: {last_err}",
            duration_ms=duration_ms, model=self.model,
        )

    # ----- internals -----
    def _sleep_backoff(self, attempt: int) -> None:
        if attempt >= self._max_retries or self._backoff <= 0:
            return
        time.sleep(self._backoff * (2 ** attempt))

    def _parse(self, raw: bytes, t0: float, metric_id: str) -> Any:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        try:
            payload = json.loads(raw)
        except Exception as exc:
            return UnscoredResult(
                reason=f"judge response not JSON: {exc}",
                duration_ms=duration_ms, model=self.model,
            )

        try:
            content = payload["choices"][0]["message"]["content"]
            parsed  = json.loads(content)
            score   = int(parsed.get("score", 0))
        except Exception as exc:
            return UnscoredResult(
                reason=f"judge response shape invalid: {exc}",
                duration_ms=duration_ms, model=self.model,
            )

        score = max(self.LIKERT_MIN, min(self.LIKERT_MAX, score))
        usage = payload.get("usage") or {}
        return JudgeResult(
            score=int(score),
            rationale=str(parsed.get("rationale", "")),
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            duration_ms=duration_ms,
            model=self.model,
            raw=payload,
        )
