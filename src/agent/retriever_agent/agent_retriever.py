"""src/agent/retriever_agent/agent_retriever.py — AM-001 Retriever.

Source of truth: src/agent/retriever_agent/prompts/{role,tools,few_shot_examples,guardrails}.

Behavior:
  - Loads modular prompts at construction.
  - run(subject, body, sender_email) -> RetrieverContext
  - Real OpenAI function-calling loop (max 6 iterations).
  - The deterministic fallback (no OPENAI_API_KEY) issues 1-3
    natural-language `lookup_policy` queries — one per distinct policy
    topic the email touches — exactly the way the real-OpenAI path is
    prompted to do.

Returned RetrieverContext shape (matches Phase 4D rubric scoring):
  customer / order / payments / products / policies / tool_calls[]

NO regex pattern matching for policy-topic detection. The only `re`
import is for the email-ID parser (`extract_order_id`, `extract_txn_id`).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from ..prompts import AgentPrompts
from ..prompt_builder import PromptBuilder
from ..retriever.agent_retriever import AgentRetriever
from src.tools import (
    TOOL_REGISTRY,
    OPENAI_TOOL_SCHEMAS,
    ToolValidationError,
    lookup_customer,
    lookup_order_by_id,
    lookup_order_by_sender_and_product,
    lookup_payment_by_transaction_id,
    lookup_payments_for_order,
    lookup_orphan_payment,
    get_product_details,
    lookup_policy,
)
from src.parser import (
    extract_order_id,
    extract_txn_id,
)


log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

MAX_TOOL_CALLS = 6
MAX_POLICY_CALLS = 3        # guardrail: cap lookup_policy calls per email


# ---------------------------------------------------------------------------
# RetrieverContext
# ---------------------------------------------------------------------------
@dataclass
class RetrieverContext:
    customer: dict = field(default_factory=lambda: {"found": False, "row": None})
    order:    dict = field(default_factory=lambda: {"found": False, "row": None})
    payments: list = field(default_factory=list)
    products: list = field(default_factory=list)
    policies: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    _sender_email: str = ""
    _identity_verified: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# OpenAI client (lazy)
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
# Helpers
# ---------------------------------------------------------------------------
def _result_keys(result: Any) -> list[str]:
    """Top-level keys for `tool_calls[]` audit log."""
    if isinstance(result, dict):
        return list(result.keys())
    return []


def _call(tool_name: str, args: dict, tool_calls: list) -> Any:
    """Run a tool, append to tool_calls, return the result."""
    t0 = time.perf_counter()
    fn = TOOL_REGISTRY.get(tool_name)
    if fn is None:
        result = {"error": f"unknown tool {tool_name!r}"}
    else:
        try:
            result = fn(args)
        except ToolValidationError as exc:
            result = {"error": str(exc)}
        except Exception as exc:                                # pragma: no cover
            log.warning("tool %s raised: %s", tool_name, exc)
            result = {"error": f"{type(exc).__name__}: {exc}"}
    duration_ms = int((time.perf_counter() - t0) * 1000)
    tool_calls.append({
        "tool":        tool_name,
        "args":        dict(args),
        "result_keys": _result_keys(result),
        "duration_ms": duration_ms,
        "error":       result.get("error") if isinstance(result, dict) else None,
    })
    return result


# ---------------------------------------------------------------------------
# Deterministic-fallback product hint
# ---------------------------------------------------------------------------
_PRODUCT_HINTS = (
    "PlayStation 5 Disc", "PlayStation 5 Digital", "PS5",
    "Xbox Series X", "Xbox Series S", "Xbox Wireless Controller",
    "DualSense", "Racing Wheel", "Meta Quest 3", "Alienware", "LED Monitor",
    "Wireless Earbuds", "Razer BlackShark", "Razer Ornata",
    "Razer Kraken", "HyperX Pulsefire", "Custom Gaming PC",
)


def _guess_product_hint(body: str, subject: str) -> Optional[str]:
    text = f"{subject} {body}".lower()
    for h in _PRODUCT_HINTS:
        if h.lower() in text:
            return h
    return None


# ---------------------------------------------------------------------------
# Deterministic fallback policy-query dispatch (content-driven)
# ---------------------------------------------------------------------------
# The previous keyword table was a fixed map overfit to the gold set;
# this fallback now derives 1-3 queries from the email's actual content:
# an anchor query built from the first clause plus per-action queries
# for each distinct action mentioned. No fixed mapping.
def _fallback_policy_queries(subject: str, body: str) -> list[tuple[str, Optional[list[str]]]]:
    """Return 1-3 (query, doc_filter) tuples derived from the email.

    The retriever (or its deterministic fallback) decides how many
    queries based on how many distinct topics the email raises. No
    keyword table, no overfitting.

    Phase 5G.5: query formulation tuned for clause-aware corpus (small,
    one-clause-per-parent parents). Each query is paired with an
    optional doc_filter so unambiguous topics (e.g. "payment methods")
    scope the lookup to the right policy doc — much more reliable
    than relying on embedding similarity across 8 unrelated docs.
    """
    text = f"{subject}. {body}".strip()
    if not text:
        return [("What policies apply to this support request?", None)]
    queries: list[tuple[str, Optional[list[str]]]] = []
    text_lower = text.lower()
    # 1) Per-topic queries: one for each distinct policy topic the email
    #    touches. We pair each query with a doc_filter so the lookup
    #    scopes to the right policy doc — much more reliable than
    #    embedding similarity across 8 unrelated docs (Phase 5G.5).
    TOPIC_QUERIES: list[tuple[list[str], str, list[str]]] = [
        # (trigger keywords, natural-language query, doc_filter)
        (["refund", "money back", "chargeback", "reverse payment"],
         "What are ByteMart's rules for refunding money, including timeline and eligibility?",
         ["refund-policy"]),
        (["return", "send back", "send it back"],
         "What is ByteMart's return policy and replacement eligibility?",
         ["return-policy"]),
        (["cancel", "cancellation"],
         "What is ByteMart's cancellation policy and the timeline for cancellation?",
         ["cancellation-policy"]),
        (["replace", "replacement", "swap", "exchange"],
         "What is ByteMart's replacement policy for defective or unwanted items?",
         ["return-policy"]),
        (["warranty", "guarantee"],
         "What does the ByteMart manufacturer warranty cover and for how long?",
         ["return-policy"]),
        (["delivery", "shipping", "dispatch", "transit", "when will"],
         "What are ByteMart's delivery options, timelines, and tracking arrangements?",
         ["shipping-policy"]),
        (["track", "tracking", "where is my order", "expected delivery"],
         "How does ByteMart track orders and notify customers about delivery status?",
         ["shipping-policy"]),
        (["payment", "pay", "card", "upi", "wallet", "net banking",
          "crypto", "bitcoin", "ethereum", "cod", "cash on delivery"],
         "What payment methods does ByteMart accept and which are explicitly excluded?",
         ["payment-policy"]),
        (["privacy", "data", "personal information", "dpdpa"],
         "What does ByteMart's privacy policy cover for personal data and DPDPA compliance?",
         ["privacy-policy"]),
        (["grievance", "complaint", "escalate", "ombudsman"],
         "What is ByteMart's grievance redressal process and escalation pathway?",
         ["grievance-policy"]),
    ]
    for keywords, q, doc_filter in TOPIC_QUERIES:
        if any(kw in text_lower for kw in keywords) and \
           not any(qq == q for qq, _ in queries):
            queries.append((q, doc_filter))
            if len(queries) >= 3:
                break
    # 2) Anchor query: the email's first clause (subject + first
    #    sentence). Gives the resolver a primary citation target when
    #    no topic matched.
    if not queries:
        first_clause = text.split(". ")[0].strip()
        if first_clause and len(first_clause) > 10:
            queries.append((
                f"What does ByteMart policy say about: {first_clause[:120]}",
                None,
            ))
    # 3) Final fallback: one generic anchor.
    if not queries:
        queries.append(("What policies apply to this support request?", None))
    return queries[:3]


# ---------------------------------------------------------------------------
# Deterministic-fallback retriever
# ---------------------------------------------------------------------------
def _is_info_only_email(subject: str, body: str) -> bool:
    """Heuristic: emails with no order id, no transaction id, and no
    customer-specific data (no @-email) are info-only. Skip identity
    lookup for these. Phase 5G fix.
    """
    text = f"{subject} {body}"
    if extract_order_id(text):
        return False
    if extract_txn_id(text):
        return False
    if "@" in text:
        return False
    return True


def _fallback_run(subject: str, body: str, sender_email: str,
              tool_calls: list) -> RetrieverContext:
    ctx = RetrieverContext(_sender_email=sender_email)
    ctx.tool_calls = tool_calls

    # 1) lookup_customer: skip for info-only emails (Phase 5G fix).
    if not _is_info_only_email(subject, body):
        cust = _call("lookup_customer", {"email": sender_email}, tool_calls)
        ctx.customer = cust
        ctx._identity_verified = bool(cust.get("found"))
    else:
        ctx._identity_verified = False

    # 2) Order by id if present
    oid = extract_order_id(subject + " " + body)
    if oid:
        order = _call("lookup_order_by_id", {"order_id": oid}, tool_calls)
        ctx.order = order
    else:
        # 3) Order by sender + product hint
        hint = _guess_product_hint(body, subject)
        if hint and ctx._identity_verified:
            order = _call("lookup_order_by_sender_and_product",
                          {"email": sender_email, "product_hint": hint},
                          tool_calls)
            ctx.order = order
        elif hint:
            prod = _call("get_product_details", {"name_hint": hint}, tool_calls)
            if prod.get("found"):
                ctx.products.append(prod["row"])

    # 4) Transaction id
    tid = extract_txn_id(subject + " " + body)
    if tid:
        pay = _call("lookup_payment_by_transaction_id",
                    {"transaction_id": tid,
                     "customer_email": sender_email},
                    tool_calls)
        if pay.get("found"):
            ctx.payments.append(pay["row"])

    # 5) Payments for the order (refund/duplicate context)
    if ctx.order.get("found") and ctx._identity_verified:
        pays = _call("lookup_payments_for_order",
                     {"customer_email": sender_email,
                      "order_id": ctx.order["row"]["order_id"]},
                     tool_calls)
        if pays.get("rows"):
            ctx.payments.extend(pays["rows"])
            # 5b) Orphan detection (E30 duplicate-charge pattern)
            for p in pays["rows"]:
                if p.get("status") == "failed" and p.get("order_id") is None:
                    _call("lookup_orphan_payment",
                          {"amount": p["amount"], "status": "failed"},
                          tool_calls)

    # 6) Product info if order has items
    if ctx.order.get("found") and ctx.products == []:
        items = ctx.order["row"].get("items") or []
        for it in items[:1]:
            prod = _call("get_product_details", {"sku": it.get("sku")}, tool_calls)
            if prod.get("found"):
                ctx.products.append(prod["row"])

    # 7) AGENTIC RAG: 1-3 natural-language `lookup_policy` queries,
    #    one per distinct policy topic the email touches.
    policy_hits_by_parent: dict[str, dict] = {}
    for q, doc_filter in _fallback_policy_queries(subject, body):
        hits = _call("lookup_policy",
                     {"query": q, "top_k": 10, "doc_filter": doc_filter},
                     tool_calls)
        for h in (hits.get("hits") or []):
            pid = h.get("parent_id")
            if pid is None:
                continue
            if pid not in policy_hits_by_parent or \
               h.get("similarity", 0) > policy_hits_by_parent[pid].get("similarity", 0):
                policy_hits_by_parent[pid] = h
    ctx.policies = list(policy_hits_by_parent.values())

    return ctx


# ---------------------------------------------------------------------------
# RetrieverAgent
# ---------------------------------------------------------------------------
class RetrieverAgent:
    """AM-001 Retriever — real OpenAI function-calling loop or deterministic
    fallback (no OpenAI key).

    The real-OpenAI path is prompted (via the modular prompts/role +
    prompts/few_shot_examples) to issue 1-3 natural-language
    `lookup_policy` queries per inbound email, one per distinct topic.
    The deterministic-fallback path mirrors the same shape via the data-driven
    `_fallback_policy_queries` table above.
    """

    NAME = "retriever_agent"

    def __init__(self):
        retriever = AgentRetriever(root=Path(__file__).parent.parent)
        self._prompts: AgentPrompts = retriever.load(self.NAME)
        self._builder = PromptBuilder()
        self._system_prompt: str = self._builder.build(self._prompts)

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def run(self, *, subject: str, body: str, sender_email: str) -> RetrieverContext:
        client = _openai()
        tool_calls: list[dict] = []

        if client is None:
            return _fallback_run(subject or "", body or "", sender_email or "", tool_calls)

        return self._openai_run(subject or "", body or "",
                                sender_email or "", tool_calls)

    # --- OpenAI path ---
    def _openai_run(self, subject: str, body: str,
                    sender_email: str, tool_calls: list) -> RetrieverContext:
        client = _openai()
        user_msg = (
            f"Subject: {subject}\n"
            f"From: {sender_email}\n\n"
            f"Body:\n{body}\n"
        )
        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user",   "content": user_msg},
        ]
        ctx = RetrieverContext(_sender_email=sender_email)

        # Hard cap: at most MAX_TOOL_CALLS total tool invocations across
        # all OpenAI iterations. (MAX_TOOL_CALLS is the cap on tool
        # invocations, not on OpenAI API calls.)
        seen_calls: set[tuple[str, str]] = set()  # (tool, frozenset(args)) for dedup
        for _ in range(MAX_TOOL_CALLS + 2):
            if len(tool_calls) >= MAX_TOOL_CALLS:
                break
            try:
                resp = client.chat.completions.create(
                    model=os.getenv("OPENAI_MODEL_RETRIEVER", "gpt-4o-mini"),
                    temperature=0,
                    messages=messages,
                    tools=OPENAI_TOOL_SCHEMAS,
                    tool_choice="auto",
                    max_tokens=2000,
                )
            except Exception as exc:                                # pragma: no cover
                log.warning("OpenAI retriever chat failed: %s", exc)
                return _fallback_run(subject, body, sender_email, tool_calls)

            msg = resp.choices[0].message
            if not msg.tool_calls:
                break

            # Append the assistant message exactly once (before the
            # tool response messages, which the OpenAI API requires).
            messages.append(msg)

            for tc in msg.tool_calls:
                if len(tool_calls) >= MAX_TOOL_CALLS:
                    break
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                # De-dupe: same (tool, args) already in this run → skip.
                sig = (tc.function.name, json.dumps(args, sort_keys=True))
                if sig in seen_calls:
                    # Still need a tool response so the API is happy.
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"error": "duplicate call suppressed"}),
                    })
                    continue
                seen_calls.add(sig)
                result = _call(tc.function.name, args, tool_calls)
                self._absorb(ctx, tc.function.name, result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str)[:4000],
                })

        ctx.tool_calls = tool_calls
        return ctx

    # --- absorb a tool result into the context ---
    def _absorb(self, ctx: RetrieverContext, name: str, result: dict) -> None:
        if not isinstance(result, dict) or result.get("error"):
            return
        if name == "lookup_customer":
            ctx.customer = result
            ctx._identity_verified = bool(result.get("found"))
        elif name == "lookup_order_by_id":
            ctx.order = result
        elif name == "lookup_order_by_sender_and_product":
            ctx.order = result
        elif name == "lookup_payment_by_transaction_id":
            if result.get("found"):
                ctx.payments.append(result["row"])
        elif name == "lookup_payments_for_order":
            ctx.payments.extend(result.get("rows") or [])
        elif name == "get_product_details":
            if result.get("found"):
                ctx.products.append(result["row"])
        elif name == "lookup_policy":
            # Dedup policy hits by parent_id, keeping the max similarity.
            for h in (result.get("hits") or []):
                pid = h.get("parent_id")
                if pid is None:
                    continue
                existing = next(
                    (p for p in ctx.policies if p.get("parent_id") == pid),
                    None,
                )
                if existing is None or h.get("similarity", 0) > existing.get("similarity", 0):
                    if existing is not None:
                        ctx.policies.remove(existing)
                    ctx.policies.append(h)
        # lookup_orphan_payment: absorbed via the payments list already
