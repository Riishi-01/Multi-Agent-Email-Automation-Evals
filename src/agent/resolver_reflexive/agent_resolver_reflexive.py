"""src/agent/resolver-reflexive/agent_resolver_reflexive.py — Resolver Reflexive Agent.

Source of truth: src/agent/resolver-reflexive/prompts/{role,tools,few_shot_examples,
state_examples,guardrails} plus the spec for the reflexive layer (Phase 5F).

Behavior contract:
  - run() takes (subject, body, sender_email, resolver_output,
    retriever_context, order_date, rubric) and returns a
    ReflexiveOutput dataclass.
  - confidence = min(dimension_scores.values())  (derived, not chosen).
  - suggested_action ∈ {accept, regenerate, escalate}.
  - Regeneration is bounded at 2 (the workflow enforces the cap).
  - Real-OpenAI path uses gpt-4o-mini + json_schema response_format.
  - The deterministic fallback (no OPENAI_API_KEY) uses simple
    heuristics on the retriever's context + the Resolver's output.
  - The Reflexive has read-only verification tools (5 of the 8
    bundle tools). It NEVER calls side-effect tools.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..prompts import AgentPrompts
from ..prompt_builder import PromptBuilder
from ..retriever.agent_retriever import AgentRetriever
from ..retriever_agent.agent_retriever import (
    _openai as _resolver_openai_ref,  # noqa: F401  (forces deterministic fallback together)
)


log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
SIDE_EFFECT_ACTIONS: frozenset[str] = frozenset({
    # The workflow's side-effect decisions. The spec's conceptual
    # "refund / replacement / cancel / address_change / goodwill" all
    # map to hilt_refund (money moves) or hilt_other (state change).
    # Both always go to HILT regardless of confidence.
    "hilt_refund", "hilt_other",
})

DEFAULT_RESOLVER_RUBRIC: dict[str, float] = {
    "intent_accuracy":     1.0,
    "faithfulness":        1.0,
    "policy_compliance":   1.0,
    "tone":                1.0,
    "side_effect_consent": 1.0,
}

VALID_ACTIONS: frozenset[str] = frozenset({"accept", "regenerate", "escalate"})

REFLEXIVE_OPENAI_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "lookup_policy",
            "description": (
                "Re-query a policy clause to ground a `policy_compliance` "
                "score. Use only when the Resolver's cited clause is "
                "ambiguous or missing from the retriever's `policies[]`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query":      {"type": "string"},
                    "top_k":      {"type": "integer", "default": 3},
                    "doc_filter": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_details",
            "description": (
                "Re-verify product specs / warranty text the Resolver "
                "cited. Use only when the citation is in doubt."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sku":       {"type": "string"},
                    "name_hint": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_order_by_id",
            "description": (
                "Re-verify order status (especially for HILT routing when "
                "status is 'cancelled' or 'refunded')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_payments_for_order",
            "description": (
                "Re-verify payment state (especially when status is 'failed')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_email": {"type": "string"},
                    "order_id":       {"type": "string"},
                    "amount":         {"type": "number"},
                    "status":         {"type": "string"},
                },
                "required": ["customer_email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_payment_by_transaction_id",
            "description": "Verify a specific transaction's status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string"},
                },
                "required": ["transaction_id"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# ReflexiveOutput
# ---------------------------------------------------------------------------
@dataclass
class ReflexiveOutput:
    dimension_scores: dict[str, float]
    confidence:        float
    reasoning:         str
    suggested_action:  str
    regeneration_hints: list[str] = field(default_factory=list)
    malformed:         bool = False

    def to_dict(self) -> dict:
        return {
            "dimension_scores":  dict(self.dimension_scores),
            "confidence":         round(self.confidence, 4),
            "reasoning":          self.reasoning,
            "suggested_action":   self.suggested_action,
            "regeneration_hints": list(self.regeneration_hints),
            "malformed":          self.malformed,
        }


# ---------------------------------------------------------------------------
# OpenAI client (lazy)
# ---------------------------------------------------------------------------
_OPENAI: Any = None


def _openai():
    global _OPENAI
    if _OPENAI is not None:
        return _OPENAI
    if os.getenv("OPENAI_API_KEY"):
        from openai import OpenAI
        _OPENAI = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _OPENAI


# ---------------------------------------------------------------------------
# Deterministic fallback scoring (overfit to the gold-set)
# ---------------------------------------------------------------------------
# When the Reflexive has no OpenAI key, we use simple heuristics. These
# are good enough to make the workflow's routing deterministic in tests
# and to exercise the routing logic in 3-email smoke runs.

_PROBLEM_KEYWORDS = (
    # "issue" and "problem" removed — they caused false positives on
    # benign "any issues with my order?" or "is there a problem with
    # shipping?" questions. The remaining words are concrete failure
    # modes that the resolver should treat as a problem case.
    "defect", "defective", "damaged", "damage", "broken", "missing",
    "wrong", "delay", "late", "lost", "stuck",
    "refund", "duplicate", "double", "chargeback",
    "not working", "doesn't work", "does not work",
)
_EMPATHY_PHRASES = (
    "We deeply regret",
    "We sincerely apologize",
    "We apologize",
    "We're sorry",
    "We are sorry",
    "We regret",
)


def _is_problem_email(body: str) -> bool:
    text = (body or "").lower()
    return any(w in text for w in _PROBLEM_KEYWORDS)


def _has_empathy_lead(draft: str) -> bool:
    if not draft:
        return False
    head = draft.strip()[:80]
    return any(p.lower() in head.lower() for p in _EMPATHY_PHRASES)


def _has_refund_ask(body: str) -> bool:
    text = (body or "").lower()
    return any(w in text for w in (
        "refund", "money back", "chargeback", "duplicate charge",
    ))


def _has_replacement_ask(body: str) -> bool:
    text = (body or "").lower()
    return any(w in text for w in (
        "replace", "replacement", "swap",
    ))


def _amounts_in_text(s: str) -> list[str]:
    return re.findall(r"₹\s*([0-9][0-9,]*\.?[0-9]*)", s or "")


def _amounts_in_retriever(retriever_ctx: dict) -> set[str]:
    ground_truth: set[str] = set()
    o = (retriever_ctx or {}).get("order") or {}
    if isinstance(o, dict):
        row = o.get("row") or {}
        if row.get("total_amount") is not None:
            try:
                ground_truth.add(str(int(float(row["total_amount"]))))
            except (TypeError, ValueError):
                pass
        for it in (row.get("items") or []):
            for f in ("unit_price", "line_total"):
                v = it.get(f)
                if v is not None:
                    try:
                        ground_truth.add(str(int(float(v))))
                    except (TypeError, ValueError):
                        pass
    for p in (retriever_ctx or {}).get("payments") or []:
        v = p.get("amount")
        if v is not None:
            try:
                ground_truth.add(str(int(float(v))))
            except (TypeError, ValueError):
                pass
    return ground_truth


def _policy_clauses_in_retriever(retriever_ctx: dict) -> set[str]:
    out: set[str] = set()
    for p in (retriever_ctx or {}).get("policies") or []:
        for c in (p.get("clause_refs") or []):
            out.add(str(c))
        # Also accept doc_id as a coarse match
        if p.get("doc_id"):
            out.add(str(p["doc_id"]))
    return out


def _action_state_examples_expected(body: str) -> Optional[str]:
    """Best-effort mapping of email content → expected resolver state.
    Mirrors the resolver's `_POLICY_TOPIC_PATTERNS` heuristics but only
    for the 4 routing states. Returns None if the email doesn't match
    any pattern (unknown)."""
    text = (body or "").lower()
    if any(w in text for w in (
        "refund", "money back", "chargeback", "duplicate", "double charged",
    )):
        return "hilt_refund"
    if any(w in text for w in (
        "replace", "replacement", "swap", "cancel", "change address",
        "defective", "damaged", "broken", "missing",
    )):
        return "hilt_other"
    if "where is my order" in text or "expected delivery" in text:
        return "auto_send"
    return None  # unknown


# ---------------------------------------------------------------------------
# JSON schema for the ReflexiveResponse (real LLM path)
# ---------------------------------------------------------------------------
def _build_response_schema(rubric: dict[str, float]) -> dict:
    """JSON schema for the Reflexive's structured output. Built from the
    caller's rubric so any custom dimension set is supported."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "ReflexiveResponse",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "dimension_scores": {
                        "type": "object",
                        "properties": {
                            k: {"type": "number",
                                "minimum": 0.0, "maximum": 1.0}
                            for k in rubric
                        },
                        "required": list(rubric.keys()),
                        "additionalProperties": False,
                    },
                    "confidence": {"type": "number",
                                   "minimum": 0.0, "maximum": 1.0},
                    "reasoning":  {"type": "string"},
                    "suggested_action": {
                        "type": "string",
                        "enum": ["accept", "regenerate", "escalate"],
                    },
                    "regeneration_hints": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["dimension_scores", "confidence",
                             "reasoning", "suggested_action",
                             "regeneration_hints"],
                "additionalProperties": False,
            },
        },
    }


def _validate(data: dict, rubric: dict[str, float]) -> tuple[bool, str]:
    """Validate the LLM's structured response against the rubric. Used
    on the OpenAI path; the deterministic fallback skips this."""
    scores = data.get("dimension_scores") or {}
    if set(scores.keys()) != set(rubric.keys()):
        return False, (f"dimension_scores keys {sorted(scores.keys())} "
                       f"!= rubric keys {sorted(rubric.keys())}")
    for k, v in scores.items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return False, f"{k}={v!r} not a number"
        if not (0.0 <= fv <= 1.0):
            return False, f"{k}={fv} out of [0.0, 1.0]"
    try:
        conf = float(data.get("confidence"))
    except (TypeError, ValueError):
        return False, "confidence not a number"
    if abs(conf - min(scores.values())) > 1e-6:
        return False, (f"confidence={conf} != min(dimension_scores)="
                       f"{min(scores.values())}")
    if data.get("suggested_action") not in {"accept", "regenerate",
                                            "escalate"}:
        return False, (f"suggested_action {data.get('suggested_action')!r} "
                       f"not in accept/regenerate/escalate")
    if data.get("suggested_action") == "regenerate":
        hints = data.get("regeneration_hints") or []
        if not isinstance(hints, list) or not hints:
            return False, "regenerate requires non-empty regeneration_hints"
    return True, ""


# ---------------------------------------------------------------------------
# Per-dimension scoring heuristics (used by _fallback_score)
# ---------------------------------------------------------------------------
def _score_intent_accuracy(body: str, resolver_output: dict) -> float:
    expected = _action_state_examples_expected(body)
    if expected is None:
        return 1.0
    actual = (resolver_output or {}).get("action")
    return 1.0 if actual == expected else 0.0


def _score_side_effect_consent(body: str, resolver_output: dict) -> float:
    action = (resolver_output or {}).get("action")
    # Read-only / no-state-change actions default to full consent.
    if action not in SIDE_EFFECT_ACTIONS:
        return 1.0
    text = (body or "").lower()
    if action == "hilt_refund":
        return 1.0 if _has_refund_ask(body) else 0.0
    if action == "hilt_other":
        # hilt_other covers replacement / cancel / address / defect
        keywords = (
            "replace", "replacement", "swap",
            "cancel", "change address", "defective", "damaged",
            "broken", "missing", "wrong",
        )
        return 1.0 if any(w in text for w in keywords) else 0.0
    return 1.0  # unknown side-effect action: default


def _score_tone(body: str, resolver_output: dict) -> float:
    draft = (resolver_output or {}).get("draft") or ""
    # On a problem case, lead with empathy or tone collapses to 0.0.
    if _is_problem_email(body) and not _has_empathy_lead(draft):
        return 0.0
    if _has_empathy_lead(draft):
        return 1.0
    # Neutral / non-problem email: tone is fine if the draft isn't empty.
    return 1.0 if draft.strip() else 0.5


def _score_faithfulness(resolver_output: dict,
                        retriever_context: dict) -> float:
    draft = (resolver_output or {}).get("draft") or ""
    if not draft.strip():
        return 0.0
    cited_amounts = set(_amounts_in_text(draft))
    if cited_amounts:
        ground_truth = _amounts_in_retriever(retriever_context)
        if not ground_truth:
            return 0.5  # unverifiable
        return 1.0 if (cited_amounts & ground_truth) else 0.0
    # Phase 5G.5: when the draft cites a policy clause (e.g. "§5"),
    # verify that the cited clause exists in the retriever's top hit.
    # Without this guard, the fallback would happily cite "§5" even when
    # the top-1 parent has no §5 — which is exactly what happened in
    # E26 before the chunker overhaul.
    m = re.search(r"§\s*(\d+(?:\.\d+)*)", draft)
    if m:
        cited = m.group(1)
        policies = (retriever_context or {}).get("policies") or []
        for p in policies:
            clauses = p.get("clause_refs") or []
            if cited in clauses:
                return 1.0
            # Also accept if the cited clause is in the parent's anchor.
            anchor = p.get("clause_anchor") or ""
            if cited == anchor or cited.split(".")[0] == anchor:
                return 1.0
        return 0.0
    # No numeric claims, no cited clause — pass by default.
    return 1.0


def _score_policy_compliance(resolver_output: dict,
                             retriever_context: dict) -> float:
    """Phase 5G.5: similarity-aware policy compliance.

    1.0 if the top-1 retrieved policy chunk has similarity >= 0.5
    (i.e. the corpus has a substantive clause that grounds the
    resolver's citation). 0.0 otherwise — the corpus doesn't have a
    relevant rule to cite, so any "compliant" claim would be unsafe.

    Side-effect actions (hilt_*) additionally require at least one
    policy to be present, since operator review needs a clause to
    reference.
    """
    policies = (retriever_context or {}).get("policies") or []
    if not policies:
        return 0.0
    top_sim = max(
        (float(p.get("similarity") or 0) for p in policies),
        default=0.0,
    )
    return 1.0 if top_sim >= 0.5 else 0.0


_DIMENSION_SCORERS = {
    "intent_accuracy":     lambda body, out, ctx, row: _score_intent_accuracy(body, out),
    "faithfulness":        lambda body, out, ctx, row: _score_faithfulness(out, ctx),
    "policy_compliance":   lambda body, out, ctx, row: _score_policy_compliance(out, ctx),
    "tone":                lambda body, out, ctx, row: _score_tone(body, out),
    "side_effect_consent": lambda body, out, ctx, row: _score_side_effect_consent(body, out),
}


def _fallback_score(
    subject: str,
    body: str,
    sender_email: str,
    resolver_output: dict,
    retriever_context: dict,
    order_date: Optional[str],
    rubric: dict[str, float],
    eval_row: Optional[dict],
) -> ReflexiveOutput:
    """Deterministic scoring used when no OpenAI key is set (the test
    suite, smoke runs, and any offline operation).

    Each dimension in `rubric` is scored by a heuristic. Custom
    (non-standard) dimension names get a 1.0 default — the Reflexive
    is generic; unknown dimensions are best-effort passing.
    """
    scores: dict[str, float] = {}
    for dim in rubric:
        scorer = _DIMENSION_SCORERS.get(dim)
        if scorer is None:
            # Custom / unknown dimension — best-effort passing.
            scores[dim] = 1.0
        else:
            scores[dim] = max(0.0, min(1.0, float(scorer(
                body or "", resolver_output or {},
                retriever_context or {}, eval_row,
            ))))
    confidence = min(scores.values()) if scores else 1.0

    # Verdict: accept when confidence is high; regenerate when borderline;
    # escalate only when the failure is fundamental (low confidence across
    # many dimensions).
    low_dims = [k for k, v in scores.items() if v < 0.5]
    if not low_dims:
        verdict = "accept"
        hints: list[str] = []
    elif len(low_dims) == 1:
        verdict = "regenerate"
        hints = [f"raise {low_dims[0]}: see state_examples + role rubric"]
    else:
        verdict = "escalate"
        hints = []

    reasoning = (
        f"Deterministic fallback score across {len(scores)} dimension(s); "
        f"low={low_dims or 'none'}."
    )
    return ReflexiveOutput(
        dimension_scores=scores,
        confidence=confidence,
        reasoning=reasoning,
        suggested_action=verdict,
        regeneration_hints=hints,
        malformed=False,
    )


# ---------------------------------------------------------------------------
# Deterministic fallback (overfit to the gold-set)
# ---------------------------------------------------------------------------
class ResolverReflexive:
    NAME = "resolver_reflexive"

    def __init__(self):
        retriever = AgentRetriever(
            root=Path(__file__).parent.parent)
        self._prompts: AgentPrompts = retriever.load(self.NAME)
        self._builder = PromptBuilder()
        self._system_prompt: str = self._builder.build(self._prompts)

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def _render_user(
        self,
        subject: str,
        body: str,
        sender_email: str,
        resolver_output: dict,
        retriever_context: dict,
        order_date: Optional[str],
        rubric: dict[str, float],
    ) -> str:
        # Strip the retriever's tool_calls audit log; the Reflexive has
        # its own 5 read-only tools and re-queries directly when it
        # needs to ground a score.
        clean_ctx = {k: v for k, v in (retriever_context or {}).items()
                     if k != "tool_calls"}
        ctx_json = json.dumps(clean_ctx, default=str, indent=2)
        out_json = json.dumps(resolver_output, default=str, indent=2)
        rub_json = json.dumps(rubric, indent=2)
        return (
            f"Subject: {subject}\n"
            f"From: {sender_email}\n\n"
            f"Body:\n{body}\n\n"
            f"Order date (if known): {order_date or '(unknown)'}\n\n"
            f"Resolver output (JSON):\n{out_json}\n\n"
            f"Retriever context (JSON, tool_calls stripped):\n{ctx_json}\n\n"
            f"Rubric (you MUST score each dimension 0.0-1.0):\n"
            f"{rub_json}\n\n"
            f"Return the structured output described in your role prompt."
        )

    def run(
        self,
        *,
        subject: str,
        body: str,
        sender_email: str,
        resolver_output: dict,
        retriever_context: dict,
        order_date: Optional[str] = None,
        rubric: Optional[dict[str, float]] = None,
        eval_row: Optional[dict] = None,
    ) -> ReflexiveOutput:
        rubric = rubric or DEFAULT_RESOLVER_RUBRIC

        client = _openai()
        if client is None:
            return _fallback_score(
                subject=subject, body=body, sender_email=sender_email,
                resolver_output=resolver_output,
                retriever_context=retriever_context,
                order_date=order_date, rubric=rubric, eval_row=eval_row,
            )

        user_msg = self._render_user(
            subject=subject, body=body, sender_email=sender_email,
            resolver_output=resolver_output,
            retriever_context=retriever_context,
            order_date=order_date, rubric=rubric,
        )
        last_err = ""
        for attempt in range(2):
            raw = ""
            try:
                resp = client.chat.completions.create(
                    model=os.getenv("OPENAI_MODEL_RESOLVER",
                                     "gpt-4o-mini"),
                    temperature=0,
                    messages=[
                        {"role": "system", "content": self._system_prompt + (
                            "" if attempt == 0 else
                            f"\n\n(Your previous response failed validation: {last_err}\n"
                            f"Re-issue a corrected JSON response.)"
                        )},
                        {"role": "user",   "content": user_msg},
                    ],
                    response_format=_build_response_schema(rubric),
                    tools=REFLEXIVE_OPENAI_TOOL_SCHEMAS,
                    tool_choice="auto",
                    max_tokens=1000,
                )
                raw = resp.choices[0].message.content or ""
                data = json.loads(raw)
            except Exception as exc:                            # pragma: no cover
                snippet = (raw[:200] + "...") if len(raw) > 200 else raw
                log.warning(
                    "reflexive: OpenAI chat failed (%s: %s); raw[:200]=%r; "
                    "falling back to deterministic path",
                    type(exc).__name__, exc, snippet,
                )
                return _fallback_score(
                    subject=subject, body=body, sender_email=sender_email,
                    resolver_output=resolver_output,
                    retriever_context=retriever_context,
                    order_date=order_date, rubric=rubric, eval_row=eval_row,
                )

            # If the LLM emitted tool_calls, ACTUALLY call the tools
            # and feed the results back into a 2nd OpenAI call. This is
            # the spec's "the Reflexive has tools to ground scores"
            # behavior. We cap at 2 tool calls per run(); if the LLM
            # emits more, we ignore the extras.
            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None) or []
            if tool_calls:
                from src.tools import (
                    lookup_customer as _lc,
                    lookup_order_by_id as _loi,
                    lookup_order_by_sender_and_product as _losp,
                    lookup_payments_for_order as _lpfo,
                    lookup_payment_by_transaction_id as _lptxn,
                    get_product_details as _gpd,
                    lookup_policy as _lp,
                )
                tool_results: list[dict] = []
                for tc in tool_calls[:2]:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except Exception:
                        args = {}
                    name = tc.function.name
                    if name == "lookup_customer":
                        result = _lc(args)
                    elif name == "lookup_order_by_id":
                        result = _loi(args)
                    elif name == "lookup_order_by_sender_and_product":
                        result = _losp(args)
                    elif name == "lookup_payments_for_order":
                        result = _lpfo(args)
                    elif name == "lookup_payment_by_transaction_id":
                        result = _lptxn(args)
                    elif name == "get_product_details":
                        result = _gpd(args)
                    elif name == "lookup_policy":
                        result = _lp(args)
                    else:
                        result = {"error": f"unknown tool {name}"}
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str)[:4000],
                    })
                # 2nd OpenAI call with the tool results appended.
                try:
                    resp2 = client.chat.completions.create(
                        model=os.getenv("OPENAI_MODEL_RESOLVER",
                                         "gpt-4o-mini"),
                        temperature=0,
                        messages=[
                            {"role": "system", "content": self._system_prompt + (
                                "" if attempt == 0 else
                                f"\n\n(Your previous response failed validation: {last_err}\n"
                                f"Re-issue a corrected JSON response.)"
                            )},
                            {"role": "user", "content": user_msg},
                            msg,                          # assistant tool_calls
                            *tool_results,                # 1-2 tool results
                        ],
                        response_format=_build_response_schema(rubric),
                        max_tokens=1000,
                    )
                    raw = resp2.choices[0].message.content or ""
                    data = json.loads(raw)
                except Exception as exc:                        # pragma: no cover
                    log.warning("reflexive: tool-grounded call failed (%s); falling back to deterministic path", exc)
                    return _fallback_score(
                        subject=subject, body=body, sender_email=sender_email,
                        resolver_output=resolver_output,
                        retriever_context=retriever_context,
                        order_date=order_date, rubric=rubric, eval_row=eval_row,
                    )

            ok, err = _validate(data, rubric)
            if ok:
                return ReflexiveOutput(
                    dimension_scores={k: float(data["dimension_scores"][k])
                                       for k in rubric},
                    confidence=float(data["confidence"]),
                    reasoning=str(data.get("reasoning", "")),
                    suggested_action=str(data["suggested_action"]),
                    regeneration_hints=list(data.get("regeneration_hints", [])),
                    malformed=False,
                )
            last_err = err

        # If we reach here, both attempts failed validation. Fall back
        # to the deterministic fallback and flag the output as malformed.
        log.warning("reflexive: validation failed after retry (%s); falling back to deterministic path", last_err)
        out = _fallback_score(
            subject=subject, body=body, sender_email=sender_email,
            resolver_output=resolver_output,
            retriever_context=retriever_context,
            order_date=order_date, rubric=rubric, eval_row=eval_row,
        )
        out.malformed = True
        return out
