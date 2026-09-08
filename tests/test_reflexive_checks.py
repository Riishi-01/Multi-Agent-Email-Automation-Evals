"""tests/test_reflexive_checks.py — structural RAG / tool-failure checks.

These are the deterministic short-circuits that run before the
Reflexive's LLM call. The tests verify the expected behavior on each
section 9 + section 10 condition.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Allow running this test file directly via `python -m pytest ...`
# without installing the package. Also force DEMO mode via conftest.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tests.conftest  # noqa: F401  (force DEMO mode)


class TestRetrievalChecks(unittest.TestCase):
    def test_zero_chunks_plain_question_passes(self):
        from src.agent.retrieval_checks import check
        # A plain question with no policy-bound keyword — should pass
        # through (tool fallback may still answer the question).
        r = check(
            retriever_ctx={"policies": []},
            email_body="Where is my order? Order id is BM200001.",
        )
        # Not policy-bound -> continue
        self.assertFalse(r["short_circuit"])

    def test_zero_chunks_policy_question_escalates(self):
        from src.agent.retrieval_checks import check
        r = check(
            retriever_ctx={"policies": []},
            email_body="What is your refund policy?",
        )
        self.assertTrue(r["short_circuit"])
        self.assertEqual(r["verdict"], "escalate")

    def test_zero_chunks_drone_policy_escalates(self):
        """Case E from the spec: no drone-return policy exists."""
        from src.agent.retrieval_checks import check
        r = check(
            retriever_ctx={"policies": []},
            email_body="What's your policy on drone returns?",
        )
        self.assertEqual(r["verdict"], "escalate")

    def test_all_chunks_low_similarity_hilts(self):
        from src.agent.retrieval_checks import check
        ctx = {
            "policies": [
                {"clause_refs": ["3.1"], "similarity": 0.3},
                {"clause_refs": ["3.2"], "similarity": 0.4},
            ]
        }
        r = check(retriever_ctx=ctx, email_body="refund please")
        self.assertTrue(r["short_circuit"])
        self.assertEqual(r["verdict"], "hilt")

    def test_high_similarity_chunks_pass(self):
        from src.agent.retrieval_checks import check
        ctx = {
            "policies": [
                {"clause_refs": ["3.1"], "similarity": 0.85},
                {"clause_refs": ["8.1"], "similarity": 0.9},
            ]
        }
        r = check(retriever_ctx=ctx, email_body="refund please")
        self.assertFalse(r["short_circuit"])

    def test_contradictory_versions_hilt(self):
        from src.agent.retrieval_checks import check
        # Same top-level clause number in 2+ chunks signals potential
        # contradiction (different versions of the same clause).
        # This is the only path to HILT in the new heuristic.
        ctx = {
            "policies": [
                {"clause_refs": ["3.1"]},
                {"clause_refs": ["3.2"]},
            ]
        }
        r = check(retriever_ctx=ctx, email_body="refund please")
        self.assertEqual(r["verdict"], "hilt")

    def test_distinct_top_level_clauses_pass(self):
        # The old heuristic would have HILTed this; the new one
        # correctly identifies it as a multi-section inquiry, not
        # a contradiction.
        from src.agent.retrieval_checks import check
        ctx = {
            "policies": [
                {"clause_refs": ["3.1"]},
                {"clause_refs": ["4.1"]},
                {"clause_refs": ["5.1"]},
            ]
        }
        r = check(retriever_ctx=ctx, email_body="refund please")
        self.assertNotEqual(r.get("verdict"), "hilt")


class TestToolFailureChecks(unittest.TestCase):
    def test_cancelled_order_hilts(self):
        from src.agent.tool_failure_checks import check
        ctx = {
            "order": {"found": True, "row": {
                "order_id": "BM200001",
                "current_status": "cancelled",
            }},
            "payments": [],
            "products": [],
        }
        out = {"action": "auto_send", "draft": "ok"}
        r = check(ctx, out)
        self.assertEqual(r["verdict"], "hilt")

    def test_refunded_order_hilts(self):
        from src.agent.tool_failure_checks import check
        ctx = {
            "order": {"found": True, "row": {
                "order_id": "BM200001",
                "current_status": "refunded",
            }},
            "payments": [],
            "products": [],
        }
        out = {"action": "auto_send", "draft": "ok"}
        r = check(ctx, out)
        self.assertEqual(r["verdict"], "hilt")

    def test_failed_payment_hilts(self):
        from src.agent.tool_failure_checks import check
        ctx = {
            "order": {"found": True, "row": {"order_id": "BM200001"}},
            "payments": [{"status": "failed", "amount": 5499}],
            "products": [],
        }
        out = {"action": "auto_send", "draft": "ok"}
        r = check(ctx, out)
        self.assertEqual(r["verdict"], "hilt")

    def test_product_not_found_hilts(self):
        from src.agent.tool_failure_checks import check
        # The product-not-found branch now only fires on a stock /
        # availability inquiry (vs. a defective-item or refund case
        # which has an order).
        ctx = {
            "order": {"found": False, "row": None},
            "payments": [],
            "products": [],
        }
        out = {"action": "auto_send",
               "draft": "I want to know about the PlayStation 5 controller. "
                         "Is it in stock?"}
        r = check(ctx, out)
        self.assertEqual(r["verdict"], "hilt")

    def test_product_not_found_defective_case_does_NOT_hilt(self):
        from src.agent.tool_failure_checks import check
        # E22-style case: defective product on an existing order.
        # The customer is reporting a problem, not asking about stock.
        # We should NOT HILT for "product not found" because the
        # customer already has the product (the order has it).
        ctx = {
            "order": {"found": True, "row": {"order_id": "BM200009"}},
            "payments": [],
            "products": [],
        }
        out = {"action": "hilt_other",
               "draft": "We deeply regret the inconvenience regarding "
                         "the Razer BlackShark V2 Pro you received, X. "
                         "Per return-policy §3.1, you are within the "
                         "14-day reporting window."}
        r = check(ctx, out)
        self.assertIsNone(r.get("verdict"))

    def test_multiple_product_matches_hilt(self):
        from src.agent.tool_failure_checks import check
        # Multiple matches only fire on a stock inquiry.
        ctx = {
            "order": {"found": False, "row": None},
            "payments": [],
            "products": [
                {"sku": "XCTRL-BLK", "name": "Xbox Controller Black"},
                {"sku": "XCTRL-WHT", "name": "Xbox Controller White"},
            ],
        }
        out = {"action": "auto_send",
               "draft": "Which Xbox controller do you have in stock?"}
        r = check(ctx, out)
        self.assertEqual(r["verdict"], "hilt")

    def test_no_issues_passes(self):
        from src.agent.tool_failure_checks import check
        ctx = {
            "order": {"found": True, "row": {"order_id": "BM200001"}},
            "payments": [{"status": "success"}],
            "products": [],
        }
        out = {"action": "auto_send", "draft": "ok"}
        r = check(ctx, out)
        self.assertFalse(r["short_circuit"])
