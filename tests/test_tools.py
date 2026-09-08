"""tests/test_tools.py — unit tests for src/tools.py (Phase 4C: 8 bundle tools).

Maps 1:1 to src/tools.py. DB-backed (uses `bytemart_evaluator` role
defined in setup_db.sql). Silently skipped when DB unreachable.

The T-001..T-004 alias tests are kept for back-compat; new tests
should use the bundle names directly.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


class _DBAware(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from src.db import get_session, Customer
            s = get_session("evaluator")
            n = s.query(Customer).count()
            s.close()
            cls._skip_db = n != 36
        except Exception as exc:
            print(f"[skip] DB not reachable: {exc}")
            cls._skip_db = True

    def setUp(self):
        if self._skip_db:
            self.skipTest("DB not reachable (run scripts/setup_db.py first)")


class TestT001_LookupByRegisterEmail(_DBAware):
    """T-001 alias — kept for back-compat; new code uses lookup_customer."""

    def test_returns_sanya_with_orders(self):
        from src.tools import T001_LOOKUP_BY_REGISTER_EMAIL
        r = T001_LOOKUP_BY_REGISTER_EMAIL({"email": "sanya.delhi@gmail.com"})
        self.assertTrue(r["found"])
        self.assertEqual(r["row"]["email"], "sanya.delhi@gmail.com")

    def test_returns_empty_for_unknown_email(self):
        from src.tools import T001_LOOKUP_BY_REGISTER_EMAIL
        r = T001_LOOKUP_BY_REGISTER_EMAIL({"email": "nobody@nowhere.com"})
        self.assertFalse(r["found"])


class TestT002_LookupByOrderId(_DBAware):
    """T-002 alias — kept for back-compat."""

    def test_validates_bad_input_format(self):
        from src.tools import T002_LOOKUP_BY_ORDER_ID, ToolValidationError
        for bad in ("BAD!", "BM1234", "12345678", "", "BM12345678"):
            with self.assertRaises(ToolValidationError,
                                   msg=f"should have rejected {bad!r}"):
                T002_LOOKUP_BY_ORDER_ID({"order_id": bad})

    def test_returns_order(self):
        from src.tools import T002_LOOKUP_BY_ORDER_ID
        r = T002_LOOKUP_BY_ORDER_ID({"order_id": "BM200001"})
        self.assertTrue(r["found"])
        self.assertEqual(r["row"]["order_id"], "BM200001")

    def test_case_insensitive(self):
        from src.tools import T002_LOOKUP_BY_ORDER_ID
        r_lower = T002_LOOKUP_BY_ORDER_ID({"order_id": "bm200001"})
        self.assertTrue(r_lower["found"])
        self.assertEqual(r_lower["row"]["order_id"], "BM200001")

    def test_unknown_order_returns_empty(self):
        from src.tools import T002_LOOKUP_BY_ORDER_ID
        r = T002_LOOKUP_BY_ORDER_ID({"order_id": "BM999999"})
        self.assertFalse(r["found"])


class TestT003_LookupByTransactionId(_DBAware):
    """T-003 alias — kept for back-compat."""

    def test_validates_bad_input_format(self):
        from src.tools import T003_LOOKUP_BY_TRANSACTION_ID, ToolValidationError
        for bad in ("400001", "ABCDEFGH", "", "7238498"):
            with self.assertRaises(ToolValidationError,
                                   msg=f"should have rejected {bad!r}"):
                T003_LOOKUP_BY_TRANSACTION_ID({"transaction_id": bad})

    def test_returns_failed_payment_with_null_order(self):
        from src.tools import T003_LOOKUP_BY_TRANSACTION_ID
        r = T003_LOOKUP_BY_TRANSACTION_ID({"transaction_id": "72384982"})
        self.assertTrue(r["found"])
        self.assertEqual(r["row"]["transaction_id"], "72384982")
        self.assertEqual(r["row"]["status"], "failed")
        self.assertIsNone(r["row"]["order_id"])

    def test_unknown_returns_empty(self):
        from src.tools import T003_LOOKUP_BY_TRANSACTION_ID
        r = T003_LOOKUP_BY_TRANSACTION_ID({"transaction_id": "00000000"})
        self.assertFalse(r["found"])


class TestT004_LookupPolicy(_DBAware):
    """T-004 alias — kept for back-compat; the full new tests live in
    tests/test_tools_phase4c.py."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from sqlalchemy import text as _t
            from src.db import get_session
            s = get_session("evaluator")
            n = s.execute(_t("SELECT COUNT(*) FROM app.policy_children")).scalar()
            s.close()
            cls._has_policies = n and n > 0
        except Exception:
            cls._has_policies = False

    def setUp(self):
        if self._skip_db:
            self.skipTest("DB not reachable")
        if not getattr(self, "_has_policies", False):
            self.skipTest("policy_children empty; run scripts/ingest_policies_parent_child.py")

    def test_basic_search_returns_hits(self):
        from src.tools import T004_LOOKUP_POLICY
        r = T004_LOOKUP_POLICY({"query": "force majeure shipping delay", "top_k": 3})
        self.assertGreaterEqual(len(r["hits"]), 1)

    def test_doc_filter_narrows_results(self):
        from src.tools import T004_LOOKUP_POLICY
        r = T004_LOOKUP_POLICY({"query": "refund", "top_k": 5,
                                "doc_filter": ["refund-policy"]})
        for h in r["hits"]:
            self.assertEqual(h["doc_id"], "refund-policy")

    def test_empty_query_raises(self):
        from src.tools import T004_LOOKUP_POLICY, ToolValidationError
        with self.assertRaises(ToolValidationError):
            T004_LOOKUP_POLICY({"query": "   ", "top_k": 3})

    def test_top_k_clamped_to_1_to_10(self):
        from src.tools import T004_LOOKUP_POLICY
        for bad in (0, -1, 100, 11):
            r = T004_LOOKUP_POLICY({"query": "refund", "top_k": bad})
            self.assertGreaterEqual(r["top_k"], 1)
            self.assertLessEqual(r["top_k"], 10)

    def test_clause_refs_is_list(self):
        from src.tools import T004_LOOKUP_POLICY
        r = T004_LOOKUP_POLICY({"query": "14 day return reporting window", "top_k": 3})
        for h in r["hits"]:
            self.assertIsInstance(h["clause_refs"], list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
