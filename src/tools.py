"""src/tools.py — Phase 4C: the 8 bundle-name LLM-callable tools.

Source of truth: data/completeBytemartEvalset/BytemartEvals.yaml#tool_vocabulary
plus src/parser for arg validation.

All tools execute against the bytemart_evaluator role (read-only).
Tool-call input args are validated before reaching SQL.

Bundle vocabulary (8 tools):
  1. lookup_customer(email)
  2. lookup_order_by_id(order_id)
  3. lookup_order_by_sender_and_product(email, product_hint)
  4. lookup_payment_by_transaction_id(transaction_id, customer_email=?)
  5. lookup_payments_for_order(customer_email, order_id=?, amount=?, status=?)
  6. lookup_orphan_payment(amount, status)
  7. get_product_details(sku | name_hint)
  8. lookup_policy(query, top_k=3)         [parent-child RAG]
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy import text

from src.db import get_session
from src.parser import (
    is_valid_order_id,
    is_valid_txn_id,
)
from src.rag import lookup_policy as rag_lookup_policy


log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Tool-call error type (kept simple; AM-001 sees the error string)
# ---------------------------------------------------------------------------
class ToolValidationError(ValueError):
    pass


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _query(sql: str, params: dict | None = None) -> list[dict]:
    sess = get_session("evaluator")
    try:
        result = sess.execute(text(sql), params or {})
        rows = result.mappings().all()
        return [dict(r) for r in rows]
    finally:
        sess.close()


def _stringify(value: Any) -> Any:
    from datetime import datetime, date
    from uuid import UUID
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _normalize_single(rows: list[dict]) -> dict:
    """Return {"found": bool, "row": dict|None} for a 0-or-1 result."""
    if not rows:
        return {"found": False, "row": None}
    return {"found": True, "row": {k: _stringify(v) for k, v in rows[0].items()}}


def _normalize_many(rows: list[dict]) -> dict:
    """Return {"rows": [...]} for a list result."""
    return {"rows": [{k: _stringify(v) for k, v in r.items()} for r in rows]}


# ---------------------------------------------------------------------------
# 1. lookup_customer
# ---------------------------------------------------------------------------
def lookup_customer(args: dict) -> dict:
    email = (args.get("email") or "").strip()
    if not email or "@" not in email or " " in email:
        raise ToolValidationError("email arg must look like an email address")
    rows = _query(
        "SELECT * FROM app.customers WHERE LOWER(email) = LOWER(:e)",
        {"e": email},
    )
    return _normalize_single(rows)


# ---------------------------------------------------------------------------
# 2. lookup_order_by_id
# ---------------------------------------------------------------------------
def lookup_order_by_id(args: dict) -> dict:
    oid = (args.get("order_id") or "").strip()
    if not is_valid_order_id(oid):
        raise ToolValidationError(
            f"order_id must match ^[A-Za-z]{{2}}\\d{{6}}$; got {oid!r}"
        )
    rows = _query(
        "SELECT * FROM app.orders WHERE LOWER(order_id) = LOWER(:o)",
        {"o": oid},
    )
    return _normalize_single(rows)


# ---------------------------------------------------------------------------
# 3. lookup_order_by_sender_and_product
# ---------------------------------------------------------------------------
def lookup_order_by_sender_and_product(args: dict) -> dict:
    email = (args.get("email") or "").strip()
    hint  = (args.get("product_hint") or "").strip()
    if not email or "@" not in email:
        raise ToolValidationError("email arg must look like an email address")
    if not hint:
        raise ToolValidationError("product_hint arg must be a non-empty string")

    # Search inside orders.items (jsonb) for a SKU/name that matches the hint.
    # The customer's most-recent matching order wins.
    rows = _query("""
        SELECT order_id, customer_email, items, total_amount, payment_method,
               order_date, expected_delivery, current_status, shipping_address,
               created_at, updated_at
          FROM app.orders
         WHERE LOWER(customer_email) = LOWER(:e)
           AND EXISTS (
             SELECT 1 FROM jsonb_array_elements(items) e
              WHERE LOWER(e->>'name') LIKE LOWER('%' || :hint || '%')
                 OR LOWER(e->>'sku')  LIKE LOWER('%' || :hint || '%')
           )
         ORDER BY order_date DESC
         LIMIT 1
    """, {"e": email, "hint": hint})
    return _normalize_single(rows)


# ---------------------------------------------------------------------------
# 4. lookup_payment_by_transaction_id
# ---------------------------------------------------------------------------
def lookup_payment_by_transaction_id(args: dict) -> dict:
    tid = (args.get("transaction_id") or "").strip()
    customer_email = (args.get("customer_email") or "").strip()
    if not is_valid_txn_id(tid):
        raise ToolValidationError(
            f"transaction_id must match ^\\d{{8}}$; got {tid!r}"
        )

    sql = "SELECT * FROM app.payments WHERE transaction_id = :t"
    params: dict[str, Any] = {"t": tid}
    if customer_email:
        sql += " AND LOWER(customer_email) = LOWER(:e)"
        params["e"] = customer_email
    rows = _query(sql, params)
    return _normalize_single(rows)


# ---------------------------------------------------------------------------
# 5. lookup_payments_for_order
# ---------------------------------------------------------------------------
def lookup_payments_for_order(args: dict) -> dict:
    email = (args.get("customer_email") or "").strip()
    if not email or "@" not in email:
        raise ToolValidationError("customer_email arg must look like an email address")
    oid = (args.get("order_id") or "").strip() or None
    if oid and not is_valid_order_id(oid):
        raise ToolValidationError(
            f"order_id must match ^[A-Za-z]{{2}}\\d{{6}}$; got {oid!r}"
        )
    amount = args.get("amount")
    if amount is not None:
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            raise ToolValidationError(f"amount must be numeric; got {amount!r}")
    status = (args.get("status") or "").strip() or None
    if status and status not in {"pending", "success", "failed",
                                 "refunded", "partially_refunded"}:
        raise ToolValidationError(f"status {status!r} is not a valid payment status")

    sql = "SELECT * FROM app.payments WHERE LOWER(customer_email) = LOWER(:e)"
    params: dict[str, Any] = {"e": email}
    if oid:
        sql += " AND LOWER(order_id) = LOWER(:o)"
        params["o"] = oid
    if amount is not None:
        sql += " AND amount = :amt"
        params["amt"] = amount
    if status:
        sql += " AND status = :st"
        params["st"] = status
    sql += " ORDER BY created_at DESC"
    rows = _query(sql, params)
    return _normalize_many(rows)


# ---------------------------------------------------------------------------
# 6. lookup_orphan_payment
# ---------------------------------------------------------------------------
def lookup_orphan_payment(args: dict) -> dict:
    amount = args.get("amount")
    status = (args.get("status") or "").strip()
    if amount is None:
        raise ToolValidationError("amount arg is required")
    if not status:
        raise ToolValidationError("status arg is required")
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise ToolValidationError(f"amount must be numeric; got {amount!r}")
    if status not in {"pending", "success", "failed",
                      "refunded", "partially_refunded"}:
        raise ToolValidationError(f"status {status!r} is not a valid payment status")

    rows = _query("""
        SELECT * FROM app.payments
         WHERE amount = :amt
           AND status = :st
           AND order_id IS NULL
         LIMIT 1
    """, {"amt": amount, "st": status})
    return _normalize_single(rows)


# ---------------------------------------------------------------------------
# 7. get_product_details
# ---------------------------------------------------------------------------
def get_product_details(args: dict) -> dict:
    sku = (args.get("sku") or "").strip() or None
    hint = (args.get("name_hint") or "").strip() or None
    if not sku and not hint:
        raise ToolValidationError("sku or name_hint arg is required")

    if sku:
        rows = _query("SELECT * FROM app.products WHERE sku = :s", {"s": sku})
    else:
        rows = _query("""
            SELECT * FROM app.products
             WHERE LOWER(name) LIKE LOWER('%' || :h || '%')
             ORDER BY
               CASE WHEN LOWER(name) LIKE LOWER(:h || '%') THEN 0 ELSE 1 END,
               length(name)
             LIMIT 1
        """, {"h": hint})
    return _normalize_single(rows)


# ---------------------------------------------------------------------------
# 8. lookup_policy (RAG; parent-child; top_k=3 dedupe to parents)
# ---------------------------------------------------------------------------
def lookup_policy(args: dict) -> dict:
    query  = (args.get("query") or "").strip()
    if not query:
        raise ToolValidationError("query arg must be a non-empty string")
    top_k  = int(args.get("top_k") or 3)
    top_k  = max(1, min(10, top_k))
    filter_ = args.get("doc_filter") or None
    if isinstance(filter_, str):
        filter_ = [filter_]

    hits = rag_lookup_policy(query, top_k=top_k, doc_filter=filter_)
    return {
        "query": query,
        "top_k": top_k,
        "hits": [
            {
                "parent_id":     h.parent_id,
                "doc_id":        h.doc_id,
                "page_from":     h.page_from,
                "page_to":       h.page_to,
                "similarity":    round(h.similarity, 4),
                "chunk_id":      h.chunk_id,
                "clause_refs":   h.clause_refs,
                "clause_anchor": h.clause_anchor,
                "text":          h.text,
            }
            for h in hits
        ],
    }


# ---------------------------------------------------------------------------
# Tool dispatch + OpenAI function-calling schema
# ---------------------------------------------------------------------------
TOOL_REGISTRY: dict[str, Callable[[dict], dict]] = {
    "lookup_customer":                      lookup_customer,
    "lookup_order_by_id":                   lookup_order_by_id,
    "lookup_order_by_sender_and_product":   lookup_order_by_sender_and_product,
    "lookup_payment_by_transaction_id":     lookup_payment_by_transaction_id,
    "lookup_payments_for_order":            lookup_payments_for_order,
    "lookup_orphan_payment":                lookup_orphan_payment,
    "get_product_details":                  get_product_details,
    "lookup_policy":                        lookup_policy,
}


# Backwards-compatible T-001..T-004 aliases for callers/tests that still
# reference the Phase-3 names. New code should use the bundle names.
T001_LOOKUP_BY_REGISTER_EMAIL = lookup_customer
T002_LOOKUP_BY_ORDER_ID        = lookup_order_by_id
T003_LOOKUP_BY_TRANSACTION_ID  = lookup_payment_by_transaction_id
T004_LOOKUP_POLICY             = lookup_policy
T004_RETRIEVE_POLICIES         = lookup_policy


OPENAI_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "lookup_customer",
            "description": (
                "Look up a registered ByteMart customer by their sender email. "
                "Returns the customer row. ALWAYS call this first for every email "
                "to confirm identity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string",
                              "description": "Sender email (e.g. alice@gmail.com)."}
                },
                "required": ["email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_order_by_id",
            "description": (
                "Look up a ByteMart order by its 8-char id "
                "(regex [A-Za-z]{2}\\d{6}, e.g. BM123244). "
                "Returns the order row. Use when the email body contains an order id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string",
                                 "description": "The 8-char BMxxxxxx order id."}
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_order_by_sender_and_product",
            "description": (
                "Look up an order by the sender email + a product name/SKU hint "
                "(no order_id in email). Returns the customer's most recent matching order. "
                "Use when the email mentions a product but not an order id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "email":        {"type": "string",
                                     "description": "Sender email."},
                    "product_hint": {"type": "string",
                                     "description": "Product name or SKU fragment (e.g. 'PlayStation 5', 'PS5-DISC')."},
                },
                "required": ["email", "product_hint"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_payment_by_transaction_id",
            "description": (
                "Look up a payment by its 8-digit transaction id. "
                "Use when the email references a specific transaction ID "
                "(rare; e.g. the orphan failed-payment case E28 with txn 72384982)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string",
                                       "description": "8-digit numeric txn id."},
                    "customer_email": {"type": "string",
                                       "description": "Optional sender email for disambiguation."},
                },
                "required": ["transaction_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_payments_for_order",
            "description": (
                "List the customer's payments, optionally filtered by order_id, "
                "amount, and/or status. Use when investigating a refund or "
                "duplicate-charge case."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_email": {"type": "string",
                                       "description": "Sender email."},
                    "order_id":       {"type": "string",
                                       "description": "Optional 8-char BMxxxxxx order id."},
                    "amount":         {"type": "number",
                                       "description": "Optional exact amount (₹)."},
                    "status":         {"type": "string",
                                       "enum": ["pending", "success", "failed",
                                                "refunded", "partially_refunded"],
                                       "description": "Optional payment status filter."},
                },
                "required": ["customer_email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_orphan_payment",
            "description": (
                "Find a failed payment with no order_id (an orphan). "
                "Used for duplicate-payment disputes where a failed attempt "
                "left a stray transaction."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number",
                               "description": "Exact amount in ₹ (e.g. 5499)."},
                    "status": {"type": "string",
                               "enum": ["failed", "pending", "success",
                                        "refunded", "partially_refunded"],
                               "description": "Payment status to match."},
                },
                "required": ["amount", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_details",
            "description": (
                "Look up a product by SKU or product name fragment. "
                "Returns the product row including any Products.md enrichment "
                "(warranty_text, tech_description, tech_details, stock_count)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sku":       {"type": "string",
                                  "description": "Exact SKU (e.g. PS5-DISC-001)."},
                    "name_hint": {"type": "string",
                                  "description": "Partial product name (e.g. 'PlayStation 5 Disc')."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_policy",
            "description": (
                "Retrieve the top-K most relevant ByteMart policy chunks for a "
                "given query. Uses pgvector cosine similarity over app.policy_children; "
                "results are deduped to parent contexts (~1200 tokens) so each hit "
                "carries enough text for a grounded citation. Use this when the email "
                "touches a policy topic (refund, return, cancellation, shipping, "
                "privacy, pricing, grievance, etc.)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query":      {"type": "string",
                                   "description": "The topic or question to look up."},
                    "top_k":      {"type": "integer", "default": 3,
                                   "description": "How many hits to return (1-10)."},
                    "doc_filter": {"type": "array", "items": {"type": "string"},
                                   "description": "Optional list of doc_ids to filter "
                                                   "(e.g. ['refund-policy'])."},
                },
                "required": ["query"],
            },
        },
    },
]


def run_tool(name: str, args: dict) -> dict:
    """Run a tool by name; return dict-shaped result.

    Validation errors are returned as a dict (not raised) so the LLM can
    read the error message and retry with a corrected call.
    """
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return {"error": f"unknown tool {name!r}", "tool": name}
    try:
        return {"tool": name, "result": fn(args)}
    except ToolValidationError as exc:
        return {"tool": name, "error": str(exc), "retryable": True}
    except Exception as exc:                                # pragma: no cover
        log.warning("tool %s raised: %s", name, exc)
        return {"tool": name, "error": f"{type(exc).__name__}: {exc}", "retryable": True}
