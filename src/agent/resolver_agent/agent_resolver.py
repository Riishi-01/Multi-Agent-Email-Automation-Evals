"""src/agent/resolver_agent/agent_resolver.py — AM-002 Resolver (Phase 4C).

Source of truth: src/agent/resolver_agent/prompts/{role,tools,few_shot_examples,guardrails}
plus data/completeBytemartEvalset/BytemartEvals.yaml#decision_distribution.

Behavior contract:
  - run() takes (subject, body, sender_email, context) and returns a
    ResolverResult dataclass.
  - 4-way decision: auto_send | hilt_refund | hilt_other | escalate.
  - hilt_refund / hilt_other carry a 7-field HiltReason object.
  - Real-OpenAI path uses `gpt-4o-mini` + `json_schema` response_format.
  - The deterministic fallback (no OPENAI_API_KEY) uses simple
    heuristics.
  - Reflexive retry (max 1) governed by `_validate`.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..prompts import AgentPrompts
from ..prompt_builder import PromptBuilder
from ..retriever.agent_retriever import AgentRetriever


log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Vocabulary (Phase 4C bundle)
# ---------------------------------------------------------------------------
INTENT_ENUM = [
    "info", "logistic", "refund", "complaint", "escalation", "other",
]
ACTION_ENUM = ("auto_send", "hilt_refund", "hilt_other", "escalate")

URGENCY_ENUM = ("low", "medium", "high")


# ---------------------------------------------------------------------------
# HiltReason dataclass (7 fields, per the bundle's hilt_reason schema)
# ---------------------------------------------------------------------------
@dataclass
class HiltReason:
    policy_clause:        str
    reason_short:         str
    reason_long:          str
    recommended_action:   str
    action_target:        str
    urgency:              str
    human_skill_required: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# ---------------------------------------------------------------------------
# ResolverResult
# ---------------------------------------------------------------------------
@dataclass
class ResolverResult:
    intent:         str
    action:         str
    draft:          Optional[str]
    self_check:     dict
    reasoning:      str
    used_entities:  dict
    hilt_reason:    Optional[HiltReason] = None

    def to_dict(self) -> dict:
        return {
            "intent":         self.intent,
            "action":         self.action,
            "draft":          self.draft,
            "self_check":     dict(self.self_check),
            "reasoning":      self.reasoning,
            "used_entities":  dict(self.used_entities),
            "hilt_reason":    self.hilt_reason.to_dict() if self.hilt_reason else None,
        }


# ---------------------------------------------------------------------------
# OpenAI helpers (lazy)
# ---------------------------------------------------------------------------
_OPENAI = None


def _openai():
    global _OPENAI
    if _OPENAI is not None:
        return _OPENAI
    if os.getenv("OPENAI_API_KEY"):
        from openai import OpenAI
        _OPENAI = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _OPENAI


# ---------------------------------------------------------------------------
# JSON schema for the ResolverResponse (real LLM path)
# ---------------------------------------------------------------------------
RESPONSE_FORMAT: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "ResolverResponse",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "intent":    {"type": "string", "enum": INTENT_ENUM},
                "action":    {"type": "string", "enum": list(ACTION_ENUM)},
                "draft":     {"type": ["string", "null"]},
                "self_check": {
                    "type": "object",
                    "properties": {
                        "policy_compliant":     {"type": "boolean"},
                        "entities_correct":     {"type": "boolean"},
                        "state_change_implied": {"type": "boolean"},
                        "tone":                  {"type": "string",
                                                  "enum": ["polite", "neutral"]},
                    },
                    "required": ["policy_compliant", "entities_correct",
                                 "state_change_implied", "tone"],
                    "additionalProperties": False,
                },
                "reasoning": {"type": "string"},
                "used_entities": {
                    "type": "object",
                    "properties": {
                        "order_id":      {"type": ["string", "null"]},
                        "txn_id":        {"type": ["string", "null"]},
                        "product_names": {"type": "array",
                                          "items": {"type": "string"}},
                    },
                    "required": ["order_id", "txn_id", "product_names"],
                    "additionalProperties": False,
                },
                "hilt_reason": {
                    "type": ["object", "null"],
                    "properties": {
                        "policy_clause":        {"type": "string"},
                        "reason_short":         {"type": "string"},
                        "reason_long":          {"type": "string"},
                        "recommended_action":   {"type": "string"},
                        "action_target":        {"type": "string"},
                        "urgency":              {"type": "string",
                                                "enum": list(URGENCY_ENUM)},
                        "human_skill_required": {"type": "string"},
                    },
                    "required": ["policy_clause", "reason_short", "reason_long",
                                 "recommended_action", "action_target",
                                 "urgency", "human_skill_required"],
                    "additionalProperties": False,
                },
            },
            "required": ["intent", "action", "draft", "self_check",
                         "reasoning", "used_entities", "hilt_reason"],
            "additionalProperties": False,
        },
    },
}


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------
def _validate(data: dict) -> tuple[bool, str]:
    if data.get("action") not in ACTION_ENUM:
        return False, f"action {data.get('action')!r} not in {ACTION_ENUM}"
    if data.get("intent") not in INTENT_ENUM:
        return False, f"intent {data.get('intent')!r} not in {INTENT_ENUM}"
    action = data["action"]
    if action in ("hilt_refund", "hilt_other"):
        d = data.get("draft")
        if not d or "[PENDING APPROVAL]" not in d:
            return False, "hilt action requires non-null draft containing [PENDING APPROVAL]"
        hr = data.get("hilt_reason")
        if not hr or not isinstance(hr, dict):
            return False, "hilt action requires hilt_reason object"
        for f in ("policy_clause", "reason_short", "reason_long",
                  "recommended_action", "action_target",
                  "urgency", "human_skill_required"):
            if not hr.get(f):
                return False, f"hilt_reason.{f} required"
        if hr["urgency"] not in URGENCY_ENUM:
            return False, f"urgency {hr['urgency']!r} not in {URGENCY_ENUM}"
    if action == "escalate":
        if data.get("draft") is not None:
            return False, "escalate action requires null draft"
        if data.get("hilt_reason") is not None:
            return False, "escalate action requires null hilt_reason"
    if action == "auto_send":
        d = data.get("draft")
        if d is None or "[PENDING APPROVAL]" in d:
            return False, "auto_send draft must be finalized and contain no placeholders"
    sc = data.get("self_check") or {}
    if not all(k in sc for k in ("policy_compliant", "entities_correct",
                                  "state_change_implied", "tone")):
        return False, "self_check missing required fields"
    return True, ""


# ---------------------------------------------------------------------------
# Deterministic fallback (no OpenAI key)
# ---------------------------------------------------------------------------
_HILT_REFUND_KW = (
    "refund", "money back", "chargeback",
    "goodwill", "discount", "gesture", "exception",
)
_HILT_OTHER_KW = (
    "replace", "replacement", "swapping",
    "cancel my order", "cancel the order", "cancel order",
    "change the delivery address", "change the shipping address",
    "change address", "change the address", "update the address",
)
_AUTO_KW = (
    "policy", "warranty", "force majeure", "dpdpa",
    "consumer forum", "delivery", "shipping", "track",
    "compatib", "specifications", "specs",
    "troubleshoot", "not turning on", "broken", "missing",
    "won't connect", "price drop", "promotion",
)


def _order_id(ctx: dict) -> Optional[str]:
    o = (ctx.get("order") or {}).get("row")
    return (o or {}).get("order_id") if o else None


def _product_names(ctx: dict) -> list[str]:
    names: list[str] = []
    o = (ctx.get("order") or {}).get("row")
    if o and o.get("items"):
        for it in o["items"]:
            sku = it.get("sku")
            if sku:
                names.append(sku)
    for p in ctx.get("products") or []:
        if p.get("sku"):
            names.append(p["sku"])
    return sorted(set(names))


def _full_name(ctx: dict) -> str:
    c = (ctx.get("customer") or {}).get("row") or {}
    return c.get("full_name") or "there"


def _fallback_run(subject: str, body: str, ctx: dict) -> ResolverResult:
    text = (subject + " " + body).lower()
    customer = (ctx.get("customer") or {}).get("row")
    order    = (ctx.get("order") or {}).get("row")
    payments = ctx.get("payments") or []
    has_order = order is not None
    has_customer = customer is not None
    has_payment = bool(payments)
    has_sender = bool(ctx.get("_sender_email"))

    # 1) escalation
    if not has_customer and not has_order and not has_payment and not has_sender:
        return _result(
            intent="escalation", action="escalate", draft=None,
            self_check={"policy_compliant": True, "entities_correct": True,
                        "state_change_implied": False, "tone": "neutral"},
            reasoning=("No identifiable customer / order / payment / sender in "
                       "context; routing to human queue (possible impersonation)."),
            used_entities=_empty_entities(),
            hilt_reason=None,
        )

    used = {
        "order_id":      _order_id(ctx),
        "txn_id":        None,
        "product_names": _product_names(ctx),
    }

    # 2) HILT_REFUND (refund / goodwill / money movement)
    if any(k in text for k in _HILT_REFUND_KW):
        return _result(
            intent="refund", action="hilt_refund",
            draft=_draft_hilt(_full_name(ctx), subject, kind="refund"),
            self_check={"policy_compliant": True, "entities_correct": True,
                        "state_change_implied": True, "tone": "neutral"},
            reasoning="Action verb matched refund trigger; routed to operator for refund execution.",
            used_entities=used,
            hilt_reason=HiltReason(
                policy_clause="refund §3",
                reason_short="Refund request requires operator approval",
                reason_long=("Customer requested a refund. Money movement requires "
                             "tier-2 support approval per refund policy §3."),
                recommended_action="review_refund_request",
                action_target="refund_execution",
                urgency="high",
                human_skill_required="tier_2_support",
            ),
        )

    # 3) HILT_OTHER (replacement / cancellation / address change)
    if any(k in text for k in _HILT_OTHER_KW):
        intent = "other"
        if "address" in text:
            intent = "logistic"
        elif "cancel" in text:
            intent = "logistic"
        elif "replace" in text or "replacement" in text:
            intent = "complaint"
        return _result(
            intent=intent, action="hilt_other",
            draft=_draft_hilt(_full_name(ctx), subject, kind="other"),
            self_check={"policy_compliant": True, "entities_correct": True,
                        "state_change_implied": True, "tone": "neutral"},
            reasoning=f"Action verb matched state-change trigger; routed to operator.",
            used_entities=used,
            hilt_reason=HiltReason(
                policy_clause="returns §2",
                reason_short="State change requires operator approval",
                reason_long=("Customer requested an order-state change "
                             "(replacement/cancellation/address). Operator approval "
                             "required before executing."),
                recommended_action="review_state_change_request",
                action_target="ops_queue",
                urgency="medium",
                human_skill_required="tier_1_support",
            ),
        )

    # 4) auto-send (intents match the 6-value spec_intent enum)
    action = "auto_send"
    if any(k in text for k in ("policy", "warranty", "force majeure",
                               "dpdpa", "consumer forum", "refund policy")):
        intent = "info"
    elif any(k in text for k in ("delivery", "shipping", "track", "expected")):
        intent = "logistic" if has_order else "info"
    elif any(k in text for k in ("compatib", "specifications", "specs")):
        intent = "info"
    elif any(k in text for k in ("troubleshoot", "not turning", "broken",
                                 "missing", "won't connect", "damaged",
                                 "defective", "wrong item")):
        intent = "complaint"
    elif "payment" in text or "charge" in text:
        intent = "info"
    elif "price drop" in text or "promotion" in text:
        intent = "info"
    else:
        intent = "info" if has_order else "other"

    return _result(
        intent=intent, action=action,
        # Phase 5G: synthesize a concrete answer from the top-1 policy
        # chunk when available, fall back to the generic filler.
        draft=_draft_info_auto_send(_full_name(ctx), subject, ctx, body=body),
        self_check={"policy_compliant": True, "entities_correct": True,
                    "state_change_implied": False, "tone": "polite"},
        reasoning=f"Classified 'info' (inquiry only); action=auto_send.",
        used_entities=used,
        hilt_reason=None,
    )


def _result(intent, action, draft, self_check, reasoning,
            used_entities, hilt_reason) -> ResolverResult:
    return ResolverResult(
        intent=intent, action=action, draft=draft,
        self_check=self_check, reasoning=reasoning,
        used_entities=used_entities, hilt_reason=hilt_reason,
    )


def _empty_entities() -> dict:
    return {"order_id": None, "txn_id": None, "product_names": []}


def _draft_hilt(full_name: str, subject: str, kind: str = "other") -> str:
    first = (full_name or "").split(" ")[0] if full_name else ""
    # Problem case: lead with empathy (per the new role/guardrails rules).
    return (
        "We deeply regret the inconvenience, "
        f"{first if first else 'thank you for your patience'}.\n\n"
        "Our team is reviewing your request and will get back to you "
        "with a resolution.\n\n"
        f"Re: [PENDING APPROVAL] {subject[:80]}\n\n"
        "[PENDING APPROVAL: review required before sending]\n\n"
        "Best regards,\nByteMart Customer Support"
    )


def _draft_auto_send(full_name: str, subject: str) -> str:
    first = (full_name or "there").split(" ")[0] or "there"
    return (
        f"Hi {first},\n\n"
        "Thank you for reaching ByteMart support.\n\n"
        f"Re: {subject}\n\n"
        "We've reviewed your message and have an update for you.\n\n"
        "Best regards,\nByteMart Customer Support"
    )


def _draft_info_auto_send(full_name: str, subject: str,
                          retriever_context: dict,
                          body: str = "") -> str:
    """Phase 5G: synthesize a concrete answer from the top-1 retrieved
    policy chunk. Prevents the "We've reviewed your message" filler when
    the retriever has substantive information to answer the question.

    Phase 5G.5: clause-aware excerpt selection — picks the clause whose
    body most overlaps with the email's terms (not just the first 200
    chars), so multi-section parents (e.g. page 32 with §3 HSN + §4 PSP
    + §5 Accepted Payment Methods) cite the right clause. We also scan
    ALL returned policies and pick the one whose best-clause match has
    the highest term overlap with the email, not just `policies[0]`.

    Also enforces a similarity floor of 0.5: if no policy is sufficiently
    relevant, fall back to the safe auto-send draft rather than citing
    a low-quality chunk.
    """
    first = (full_name or "there").split(" ")[0] or "there"
    policies = (retriever_context or {}).get("policies") or []
    if policies:
        # Similarity floor on the BEST policy overall.
        best_sim = max(
            (float(p.get("similarity") or 0) for p in policies),
            default=0.0,
        )
        if best_sim < _SIMILARITY_FLOOR:
            return _draft_auto_send(full_name, subject)

        from src.rag import best_clause_for_query
        email_terms = set(re.findall(r"\w+", f"{subject} {body}".lower()))

        # Phase 5G.5: when the email is unambiguously about payments
        # and a §5 Accepted Payment Methods parent is in the policies,
        # prefer §5.1 (the "list of accepted methods" clause) over a
        # context-setting §1.x/§4.x clause even if those have slightly
        # higher embedding similarity. The §5 clause contains the
        # actual rule the customer is asking about.
        payment_keywords = {"payment", "pay", "card", "upi", "wallet",
                            "net", "banking", "crypto", "bitcoin",
                            "ethereum", "cod", "cash", "delivery"}
        is_payment_query = bool(payment_keywords & email_terms)
        if is_payment_query:
            # Prefer the lowest-numbered §5.x anchor — §5.1 is the
            # "accepted methods" list; §5.2 is "no other methods"; §5.3+
            # is implementation detail.
            for p in sorted(
                (pp for pp in policies
                 if pp.get("doc_id") == "payment-policy"
                 and (pp.get("clause_anchor") or "").startswith("5")),
                key=lambda pp: pp.get("clause_anchor") or "",
            ):
                anchor = p.get("clause_anchor") or ""
                # Extract the §5.1-like clause section from the parent.
                clause_num, clause_text = best_clause_for_query(
                    p.get("text", "") or "", body or "",
                    subject=subject or "",
                )
                excerpt = (clause_text or "")[:300].replace("\n", " ").strip()
                return (
                    f"Hi {first},\n\n"
                    "Thank you for reaching ByteMart support.\n\n"
                    f"Re: {subject}\n\n"
                    f"Per payment-policy §{clause_num}, {excerpt}...\n\n"
                    "Best regards,\nByteMart Customer Support"
                )

        # Pick the policy whose best clause most overlaps with the
        # email's terms — not just policies[0].
        best_match: tuple[float, dict, str, str] | None = None
        for p in policies:
            parent_text = p.get("text", "") or ""
            clause_num, clause_text = best_clause_for_query(
                parent_text, body or "", subject=subject or "",
            )
            clause_terms = set(re.findall(r"\w+", clause_text.lower()))
            overlap = len(email_terms & clause_terms)
            if best_match is None or overlap > best_match[0]:
                best_match = (overlap, p, clause_num, clause_text)
        if best_match is None:
            return _draft_auto_send(full_name, subject)
        _, top, clause_num, clause_text = best_match
        excerpt = (clause_text or "")[:300].replace("\n", " ").strip()
        doc_id = top.get("doc_id", "policy") or "policy"
        clause_ref = f"§{clause_num}" if clause_num else ""
        if not clause_ref:
            clauses = top.get("clause_refs") or []
            clause_ref = f"§{clauses[0]}" if clauses else ""
        return (
            f"Hi {first},\n\n"
            "Thank you for reaching ByteMart support.\n\n"
            f"Re: {subject}\n\n"
            f"Per {doc_id} {clause_ref}, {excerpt}...\n\n"
            "Best regards,\nByteMart Customer Support"
        )
    return _draft_auto_send(full_name, subject)


# Phase 5G.5: similarity floor below which the resolver treats the top-1
# hit as not relevant enough to synthesize from. The retriever's
# structural check fires at 0.6 (src/agent/retrieval_checks.py); we use
# 0.5 here to give a small grace margin.
_SIMILARITY_FLOOR = 0.5


# ---------------------------------------------------------------------------
# Real resolver (OpenAI json_schema)
# ---------------------------------------------------------------------------
class ResolverAgent:
    """AM-002 — Resolver (reflexive + HILT-aware, 4 decisions).

    Behaviour ONLY. All prompt text lives in
    `./prompts/{role,tools,few_shot_examples,guardrails}` and is loaded
    once at construction.
    """

    NAME = "resolver_agent"

    def __init__(self):
        retriever = AgentRetriever(root=__import__("pathlib").Path(__file__).parent.parent)
        self._prompts: AgentPrompts = retriever.load(self.NAME)
        self._builder = PromptBuilder()
        self._system_prompt: str = self._builder.build(self._prompts)

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def _render_user(self, subject: str, body: str, sender_email: str,
                     ctx: dict) -> str:
        # Strip the retriever's `tool_calls` audit log from the
        # context the resolver sees. The resolver only needs the
        # resolved results (customer, order, payments, products,
        # policies) to draft — the tool-by-tool log bloats the
        # prompt and adds no drafting value.
        clean_ctx = {k: v for k, v in (ctx or {}).items()
                     if k != "tool_calls"}
        return (
            f"Subject: {subject}\n"
            f"From: {sender_email}\n\n"
            f"Body:\n{body}\n\n"
            f"Retriever context (JSON):\n"
            f"{json.dumps(clean_ctx, default=str, indent=2)}"
        )

    def run(self, *, subject: str, body: str, sender_email: str,
            context: dict) -> ResolverResult:
        client = _openai()
        if client is None:
            return _fallback_run(subject or "", body or "", context or {})

        user_msg = self._render_user(subject or "", body or "",
                                     sender_email or "", context or {})
        # Optional regeneration hints from the Reflexive. Surfaced only
        # when the caller passed them in the context under
        # `_regeneration_hints`.
        hints = (context or {}).get("_regeneration_hints") or []
        hint_block = ""
        if hints:
            hint_lines = "\n".join(f"  - {h}" for h in hints)
            hint_block = (
                "\n\n## Reflexive regeneration hints\n"
                "The Reflexive reviewed your previous response and asks "
                "that the next iteration address:\n"
                f"{hint_lines}\n"
            )
        last_err = ""
        for attempt in range(2):
            messages = [
                {"role": "system", "content": self._system_prompt + (
                    "" if attempt == 0 else
                    f"\n\n(Your previous response failed validation: {last_err}\n"
                    f"Re-issue a corrected JSON response.)"
                ) + hint_block},
                {"role": "user", "content": user_msg},
            ]
            raw = ""
            try:
                resp = client.chat.completions.create(
                    model=os.getenv("OPENAI_MODEL_RESOLVER", "gpt-4o-mini"),
                    temperature=0,
                    messages=messages,
                    response_format=RESPONSE_FORMAT,
                    # Cap output so a runaway generation doesn't truncate
                    # mid-JSON (which produces a 60k-char unparseable blob).
                    max_tokens=2000,
                )
                raw = resp.choices[0].message.content or ""
                data = json.loads(raw)
            except Exception as exc:                            # pragma: no cover
                # OpenAI call or JSON parse failed. Capture a snippet of
                # the raw response so the operator can see WHY it failed
                # (e.g. truncated mid-JSON, malformed schema); fall back
                # to the deterministic fallback so the workflow still produces a trace.
                snippet = (raw[:200] + "...") if len(raw) > 200 else raw
                log.warning(
                    "resolver: OpenAI chat failed (%s: %s); raw[:200]=%r; "
                    "falling back to deterministic path",
                    type(exc).__name__, exc, snippet,
                )
                return _fallback_run(subject or "", body or "", context or {})

            ok, err = _validate(data)
            if ok:
                hr = None
                if data.get("hilt_reason"):
                    hr = HiltReason(**data["hilt_reason"])
                return ResolverResult(
                    intent=data["intent"],
                    action=data["action"],
                    draft=data.get("draft"),
                    self_check=data["self_check"],
                    reasoning=data.get("reasoning", ""),
                    used_entities=data.get("used_entities") or _empty_entities(),
                    hilt_reason=hr,
                )
            last_err = err

        log.warning("Resolver validation failed after retry; falling back to deterministic path")
        return _fallback_run(subject or "", body or "", context or {})

    def run_with_hints(
        self,
        *,
        subject: str,
        body: str,
        sender_email: str,
        context: dict,
        hints: list,
    ) -> ResolverResult:
        """Like `run()`, but appends `hints` to the system prompt for
        regeneration calls driven by the Reflexive.

        Non-breaking addition: the existing `run()` is unchanged.
        The workflow calls this when the Reflexive's verdict is
        `regenerate` and `retry_count < MAX_RETRIES`."""
        return self.run(
            subject=subject, body=body, sender_email=sender_email,
            context={**context, "_regeneration_hints": list(hints or [])},
        )
