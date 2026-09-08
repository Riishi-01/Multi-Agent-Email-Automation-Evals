"""src/agent/tool_failure_checks.py — structural tool-failure checks (spec section 10).

Pure Python. Inspects the retriever's context and the resolver's draft
to map specific tool-failure conditions to a deterministic verdict.

Returns:
  {"short_circuit": False, "verdict": None, "reason": ""}
  {"short_circuit": True,  "verdict": "hilt" | "escalate", "reason": "..."}
"""
from __future__ import annotations

from typing import Any


# Verbs that count as a customer-initiated side-effect request
_REFUND_VERBS = (
    "refund", "money back", "chargeback", "duplicate charge",
    "double charged", "deducted but",
)
_REPLACEMENT_VERBS = (
    "replace", "replacement", "swap", "damaged", "defective",
    "broken", "not working", "doesn't work",
)
_CANCEL_VERBS = ("cancel my order", "cancel the order", "cancel order")
_ADDR_CHANGE_VERBS = ("change address", "change the address",
                     "deliver to a different address", "update the address")


def _has_verb(body: str, verbs: tuple[str, ...]) -> bool:
    text = (body or "").lower()
    return any(v in text for v in verbs)


# Words that signal a *stock / availability inquiry* (vs. a defective
# or refund case where the customer already owns a product). Only
# stock inquiries warrant the "product not found -> HILT" branch;
# defective / refund cases have an order, and the agent has the
# order's items.
#
# IMPORTANT: the old keyword list included ambiguous substrings
# like "available" and "stock" which fired on benign phrases like
# "is it possible to have it delivered" (matched "available") or
# "I paid the regular retail price" (matched "stock"). The new list
# uses whole-word / phrase matching that only fires on actual stock
# inquiries.
import re as _re
_STOCK_INQUIRY_PATTERNS = [
    _re.compile(r"\bin stock\b", _re.IGNORECASE),
    _re.compile(r"\bout of stock\b", _re.IGNORECASE),
    _re.compile(r"\brestock\b", _re.IGNORECASE),
    _re.compile(r"\bdo you have\b", _re.IGNORECASE),
    _re.compile(r"\bdo you carry\b", _re.IGNORECASE),
    _re.compile(r"\bstock for\b", _re.IGNORECASE),
    _re.compile(r"\bstock of\b", _re.IGNORECASE),
]


def check(retriever_ctx: dict, resolver_output: dict) -> dict[str, Any]:
    """Inspect the retriever's data + the resolver's draft for the
    tool-failure conditions in spec section 10."""
    order = ((retriever_ctx or {}).get("order") or {}).get("row") or {}
    payments = (retriever_ctx or {}).get("payments") or []
    draft = (resolver_output or {}).get("draft", "") or ""
    # The body for product-keyword scanning: prefer the explicit
    # `_body_for_check` slot (set by the workflow) and fall back to the
    # draft. The draft is sufficient for most cases because the
    # Resolver repeats the product name in its reply.
    body = (
        (resolver_output or {}).get("_body_for_check")
        or draft
        or ""
    )

    # Customer found, but the order they're asking about is not theirs
    # -> identity mismatch. We don't have a direct "order ownership"
    # field, but the order has customer_email. If the resolver's
    # draft doesn't reference a customer_email that matches the order's
    # customer_email, that's a soft mismatch.
    if order and "order_id" in order:
        # This is a structural check we can't easily run without the
        # incoming sender_email. The workflow passes sender_email;
        # here we only flag a generic "order present" check.
        pass

    # Cancelled or refunded order -> HILT (the resolver should not
    # produce a "fresh" response on a closed order).
    status = (order.get("current_status") or "").lower()
    if status in ("cancelled", "refunded"):
        return {
            "short_circuit": True,
            "verdict": "hilt",
            "reason": f"order status is {status}; the order is closed",
        }

    # Failed payment -> HILT (the resolver should not auto-discuss
    # the failed payment; let a human review)
    for p in payments:
        if str(p.get("status", "")).lower() == "failed":
            return {
                "short_circuit": True,
                "verdict": "hilt",
                "reason": "a payment on this order is status=failed; "
                          "HILT for human review",
            }

    # Product-not-found / multiple-product-matches: only fire when
    # the email is a *stock / availability inquiry* (the customer is
    # asking "do you have X?"). Defective / refund / order-status
    # cases have an existing order and the agent already has the order
    # items — they don't need a separate product lookup. The old
    # heuristic fired on ANY product mention, which incorrectly HILTed
    # defective-item cases like E22 ("My Razer BlackShark is defective").
    products = (retriever_ctx or {}).get("products") or []
    body_lower = body.lower()
    is_stock_inquiry = any(p.search(body_lower) for p in _STOCK_INQUIRY_PATTERNS)
    if is_stock_inquiry and not products and any(
        w in body_lower for w in
        ("playstation", "xbox", "razer", "logitech", "alienware", "meta quest",
         "controller", "blackshark", "racing wheel", "monitor",
         "headset", "mouse", "keyboard", "dualsense", "racing", "riser",
         "ps5", "xbox series", "rtx", "controller", "gpu", "cpu", "ssd",
         "earbuds", "microphone", "webcam")
    ):
        return {
            "short_circuit": True,
            "verdict": "hilt",
            "reason": "stock inquiry but retriever found no product match",
        }
    if is_stock_inquiry and len(products) > 1:
        return {
            "short_circuit": True,
            "verdict": "hilt",
            "reason": (f"stock inquiry matched {len(products)} products; "
                      "human must disambiguate"),
        }

    # Policy lookup gap: zero policies + the body asks about one.
    # This is also caught by retrieval_checks.check; here we just
    # pass it through.
    return {"short_circuit": False, "verdict": None, "reason": ""}
