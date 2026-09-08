"""src/agent/retrieval_checks.py — structural RAG checks (spec section 9).

These run BEFORE the Reflexive's LLM call. If any of them fires, the
Reflexive short-circuits with a deterministic verdict (no LLM needed).
"""
from __future__ import annotations

from typing import Any


# Policy-bound question words: if the email asks about one of these
# topics AND the retriever returns 0 chunks, escalate (the policy
# does not exist in the corpus -> unfixable).
_POLICY_BOUND_WORDS = (
    "refund policy", "return policy", "warranty policy",
    "cancellation policy", "shipping policy", "privacy policy",
    "refund", "return", "warranty", "cancel", "warranty period",
)


def check(retriever_ctx: dict, email_body: str) -> dict[str, Any]:
    """Return one of:
      {"short_circuit": False, "verdict": None, "reason": ""}
      {"short_circuit": True,  "verdict": "hilt" | "escalate", "reason": "..."}
    """
    chunks = (retriever_ctx or {}).get("policies") or []
    similarities = [c.get("similarity", 1.0) for c in chunks]

    # Case 1: zero chunks + read-only question -> continue (tool
    # fallback may still answer the question). Zero chunks + policy-
    # bound question -> escalate (the policy does not exist in
    # the corpus; this is unfixable per spec section 9).
    if not chunks:
        is_policy_q = any(w in (email_body or "").lower()
                         for w in _POLICY_BOUND_WORDS)
        if is_policy_q:
            return {
                "short_circuit": True,
                "verdict": "escalate",
                "reason": ("zero policy chunks AND email asks about a "
                           "policy topic; the policy does not exist in "
                           "the corpus -> escalate"),
            }
        return {"short_circuit": False, "verdict": None, "reason": ""}

    # Case 2: low similarity across all chunks -> HILT
    if chunks and all(s < 0.6 for s in similarities):
        return {
            "short_circuit": True,
            "verdict": "hilt",
            "reason": "all retrieved chunks have similarity < 0.6; "
                      "policy relevance is too weak",
        }

    # Case 3: contradictory chunks (different MAJOR VERSIONS of the
    # SAME clause appear together, e.g. refund §3 (2024) AND
    # refund §3 (2025)). This is genuinely contradictory and merits
    # HILT. We detect this by counting repeated clause anchors across
    # the chunks. Two chunks with the SAME (doc_id, clause_anchor)
    # means the same clause appears in 2+ different parents — only
    # possible if the corpus has version skew (e.g. §3.1 v2024 AND
    # §3.1 v2025). Multiple distinct anchors (e.g. §3.1, §3.2, §3.3)
    # are NOT contradictions — they're a multi-section inquiry, the
    # normal case for the agentic retriever.
    #
    # The old heuristic (>= 3 distinct top-level clause numbers) was
    # a false-positive on most real RAG retrievals: a normal E30
    # retrieval has chunks from §3, §8, §9, §10, §11, §12 ... which
    # are different sections of the same policy, NOT contradictions.
    #
    # Phase 5G.5: with clause-aware chunking, each parent contains a
    # single clause. Multiple parents sharing the same clause ROOT
    # (e.g. §2.1, §2.2, §2.3 all rooted at "2") is NORMAL — they're
    # sibling sub-clauses. We only flag a contradiction when two
    # parents have the SAME full clause_anchor (e.g. both §2.1).
    #
    # Backward-compat: legacy chunks without clause_anchor fall back
    # to clause_refs-only counting (the original heuristic).
    contradiction_keys: dict[tuple, int] = {}
    legacy_clause_count: dict[str, int] = {}
    for c in chunks:
        doc_id = c.get("doc_id") or ""
        anchor = c.get("clause_anchor") or ""
        if anchor:
            # Use the full clause anchor (not just the root) so that
            # sibling sub-clauses §2.1, §2.2, §2.3 don't collide.
            key = (doc_id, anchor)
            contradiction_keys[key] = contradiction_keys.get(key, 0) + 1
        else:
            # Legacy path: count clause_refs roots only.
            for cref in (c.get("clause_refs") or []):
                cref = str(cref)
                root = cref.split(".")[0] if "." in cref else cref
                legacy_clause_count[root] = legacy_clause_count.get(root, 0) + 1
    contested = [k for k, v in contradiction_keys.items() if v >= 2]
    if contested:
        return {
            "short_circuit": True,
            "verdict": "hilt",
            "reason": (f"clause(s) {sorted(contested)} appear in "
                       "multiple chunks (likely contradictory versions); "
                       "human must disambiguate"),
        }
    if legacy_clause_count:
        legacy_contested = [k for k, v in legacy_clause_count.items()
                            if v >= 2]
        if legacy_contested:
            return {
                "short_circuit": True,
                "verdict": "hilt",
                "reason": (f"clause(s) {sorted(legacy_contested)} appear "
                           "in multiple chunks (likely contradictory "
                           "versions); human must disambiguate"),
            }

    return {"short_circuit": False, "verdict": None, "reason": ""}
