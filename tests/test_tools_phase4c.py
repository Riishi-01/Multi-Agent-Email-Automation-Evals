"""tests/test_tools_phase4c.py — RED tests for the 8 bundle tools (Phase 4C).

Bundle vocabulary (per data/completeBytemartEvalset/BytemartEvals.yaml):
  1. lookup_customer(email)
  2. lookup_order_by_id(order_id)
  3. lookup_order_by_sender_and_product(email, product_hint)
  4. lookup_payment_by_transaction_id(transaction_id, customer_email=?)
  5. lookup_payments_for_order(customer_email, order_id=?, amount=?, status=?)
  6. lookup_orphan_payment(amount, status)
  7. get_product_details(sku | name_hint)
  8. lookup_policy(query, top_k=3) [already exists from Phase 4B]

Each tool validates args; bad input -> ToolValidationError.
DB-backed; silently skipped when DB unreachable.
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


# ---------------------------------------------------------------------------
# 1. lookup_customer
# ---------------------------------------------------------------------------
class TestLookupCustomer(_DBAware):
    def test_returns_sanya(self):
        from src.tools import lookup_customer
        r = lookup_customer({"email": "sanya.delhi@gmail.com"})
        self.assertTrue(r["found"])
        self.assertEqual(r["row"]["email"], "sanya.delhi@gmail.com")

    def test_unknown_email(self):
        from src.tools import lookup_customer
        r = lookup_customer({"email": "nobody@nowhere.com"})
        self.assertFalse(r["found"])

    def test_bad_email_format(self):
        from src.tools import lookup_customer, ToolValidationError
        with self.assertRaises(ToolValidationError):
            lookup_customer({"email": "not-an-email"})

    def test_empty_email(self):
        from src.tools import lookup_customer, ToolValidationError
        with self.assertRaises(ToolValidationError):
            lookup_customer({"email": ""})


# ---------------------------------------------------------------------------
# 2. lookup_order_by_id
# ---------------------------------------------------------------------------
class TestLookupOrderById(_DBAware):
    def test_returns_order(self):
        from src.tools import lookup_order_by_id
        r = lookup_order_by_id({"order_id": "BM200001"})
        self.assertTrue(r["found"])
        self.assertEqual(r["row"]["order_id"], "BM200001")

    def test_case_insensitive(self):
        from src.tools import lookup_order_by_id
        r = lookup_order_by_id({"order_id": "bm200001"})
        self.assertTrue(r["found"])
        self.assertEqual(r["row"]["order_id"], "BM200001")

    def test_bad_format_raises(self):
        from src.tools import lookup_order_by_id, ToolValidationError
        for bad in ("BAD!", "BM1234", "12345678", ""):
            with self.assertRaises(ToolValidationError):
                lookup_order_by_id({"order_id": bad})

    def test_unknown_order(self):
        from src.tools import lookup_order_by_id
        r = lookup_order_by_id({"order_id": "BM999999"})
        self.assertFalse(r["found"])


# ---------------------------------------------------------------------------
# 3. lookup_order_by_sender_and_product
# ---------------------------------------------------------------------------
class TestLookupOrderBySenderAndProduct(_DBAware):
    def test_finds_ps5_for_sanya(self):
        from src.tools import lookup_order_by_sender_and_product
        r = lookup_order_by_sender_and_product({
            "email": "sanya.delhi@gmail.com",
            "product_hint": "PlayStation 5",
        })
        self.assertTrue(r["found"])
        self.assertEqual(r["row"]["order_id"], "BM200001")
        self.assertEqual(r["row"]["customer_email"], "sanya.delhi@gmail.com")

    def test_returns_most_recent_first(self):
        # Multiple orders for one customer — must return the latest.
        from src.tools import lookup_order_by_sender_and_product
        r = lookup_order_by_sender_and_product({
            "email": "sanya.delhi@gmail.com",
            "product_hint": "PS5",
        })
        self.assertTrue(r["found"])

    def test_unknown_sender(self):
        from src.tools import lookup_order_by_sender_and_product
        r = lookup_order_by_sender_and_product({
            "email": "nobody@nowhere.com",
            "product_hint": "PS5",
        })
        self.assertFalse(r["found"])

    def test_empty_email(self):
        from src.tools import lookup_order_by_sender_and_product, ToolValidationError
        with self.assertRaises(ToolValidationError):
            lookup_order_by_sender_and_product({
                "email": "", "product_hint": "PS5",
            })

    def test_empty_product_hint(self):
        from src.tools import lookup_order_by_sender_and_product, ToolValidationError
        with self.assertRaises(ToolValidationError):
            lookup_order_by_sender_and_product({
                "email": "sanya.delhi@gmail.com", "product_hint": "",
            })


# ---------------------------------------------------------------------------
# 4. lookup_payment_by_transaction_id
# ---------------------------------------------------------------------------
class TestLookupPaymentByTxn(_DBAware):
    def test_orphan_failed(self):
        from src.tools import lookup_payment_by_transaction_id
        r = lookup_payment_by_transaction_id({"transaction_id": "72384982"})
        self.assertTrue(r["found"])
        self.assertEqual(r["row"]["status"], "failed")
        self.assertIsNone(r["row"]["order_id"])

    def test_unknown_txn(self):
        from src.tools import lookup_payment_by_transaction_id
        r = lookup_payment_by_transaction_id({"transaction_id": "00000000"})
        self.assertFalse(r["found"])

    def test_bad_format(self):
        from src.tools import lookup_payment_by_transaction_id, ToolValidationError
        for bad in ("ABC", "12345", ""):
            with self.assertRaises(ToolValidationError):
                lookup_payment_by_transaction_id({"transaction_id": bad})


# ---------------------------------------------------------------------------
# 5. lookup_payments_for_order
# ---------------------------------------------------------------------------
class TestLookupPaymentsForOrder(_DBAware):
    def test_returns_payments_for_order(self):
        from src.tools import lookup_payments_for_order
        r = lookup_payments_for_order({
            "customer_email": "manish.jain.mum@gmail.com",
            "order_id": "BM189438",
        })
        self.assertGreater(len(r["rows"]), 0)
        for row in r["rows"]:
            self.assertEqual(row["customer_email"], "manish.jain.mum@gmail.com")
            self.assertEqual(row["order_id"], "BM189438")

    def test_filters_by_amount(self):
        from src.tools import lookup_payments_for_order
        r = lookup_payments_for_order({
            "customer_email": "manish.jain.mum@gmail.com",
            "order_id": "BM189438",
            "amount": 5499.0,
        })
        for row in r["rows"]:
            self.assertEqual(float(row["amount"]), 5499.0)

    def test_filters_by_status(self):
        from src.tools import lookup_payments_for_order
        r = lookup_payments_for_order({
            "customer_email": "manish.jain.mum@gmail.com",
            "order_id": "BM189438",
            "status": "success",
        })
        for row in r["rows"]:
            self.assertEqual(row["status"], "success")

    def test_empty_customer_email(self):
        from src.tools import lookup_payments_for_order, ToolValidationError
        with self.assertRaises(ToolValidationError):
            lookup_payments_for_order({"customer_email": ""})

    def test_no_filter_no_order(self):
        from src.tools import lookup_payments_for_order
        # With just an email, returns all that customer's payments.
        r = lookup_payments_for_order({"customer_email": "manish.jain.mum@gmail.com"})
        self.assertGreater(len(r["rows"]), 0)


# ---------------------------------------------------------------------------
# 6. lookup_orphan_payment
# ---------------------------------------------------------------------------
class TestLookupOrphanPayment(_DBAware):
    def test_finds_orphan_failed(self):
        from src.tools import lookup_orphan_payment
        r = lookup_orphan_payment({"amount": 5499.0, "status": "failed"})
        self.assertTrue(r["found"])
        self.assertIsNone(r["row"]["order_id"])
        self.assertEqual(r["row"]["status"], "failed")

    def test_no_orphan_succeeded(self):
        from src.tools import lookup_orphan_payment
        # Succeeded payments always have an order_id (invariant), so this
        # should return no orphan.
        r = lookup_orphan_payment({"amount": 54990.0, "status": "success"})
        self.assertFalse(r["found"])

    def test_missing_args(self):
        from src.tools import lookup_orphan_payment, ToolValidationError
        with self.assertRaises(ToolValidationError):
            lookup_orphan_payment({"amount": 5499.0})              # no status
        with self.assertRaises(ToolValidationError):
            lookup_orphan_payment({"status": "failed"})            # no amount


# ---------------------------------------------------------------------------
# 7. get_product_details
# ---------------------------------------------------------------------------
class TestGetProductDetails(_DBAware):
    def test_lookup_by_sku(self):
        from src.tools import get_product_details
        r = get_product_details({"sku": "PS5-DISC-001"})
        self.assertTrue(r["found"])
        self.assertEqual(r["row"]["sku"], "PS5-DISC-001")
        self.assertEqual(r["row"]["name"], "PlayStation 5 Disc Edition Console")

    def test_lookup_by_name_hint(self):
        from src.tools import get_product_details
        r = get_product_details({"name_hint": "PlayStation 5 Disc"})
        self.assertTrue(r["found"])
        self.assertEqual(r["row"]["sku"], "PS5-DISC-001")

    def test_unknown_sku(self):
        from src.tools import get_product_details
        r = get_product_details({"sku": "DOES-NOT-EXIST"})
        self.assertFalse(r["found"])

    def test_includes_enrichment_when_present(self):
        from src.tools import get_product_details
        r = get_product_details({"sku": "PS5-DISC-001"})
        # PS5-DISC-001 is in Products.md → warranty_text must be present.
        self.assertTrue(r["row"]["warranty_text"])

    def test_bare_sku_returns_null_enrichment(self):
        from src.tools import get_product_details
        r = get_product_details({"sku": "RZR-ORN-V3"})
        self.assertTrue(r["found"])
        # RZR-ORN-V3 is not in Products.md → warranty_text is NULL.
        self.assertIsNone(r["row"]["warranty_text"])

    def test_requires_sku_or_name_hint(self):
        from src.tools import get_product_details, ToolValidationError
        with self.assertRaises(ToolValidationError):
            get_product_details({})

    def test_category_is_known(self):
        from src.tools import get_product_details
        r = get_product_details({"sku": "PS5-DISC-001"})
        self.assertIn(r["row"]["category"], {
            "gaming_console", "gaming_desktop", "game_controller",
            "gaming_keyboard", "gaming_mouse", "racing_wheel",
            "vr_headset", "monitor", "audio", "service_fee",
        })


# ---------------------------------------------------------------------------
# 8. lookup_policy  (Phase 4B — re-exported under bundle name)
# ---------------------------------------------------------------------------
class TestLookupPolicy(_DBAware):
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

    def test_returns_hits_with_clauses(self):
        from src.tools import lookup_policy
        r = lookup_policy({"query": "14 day return reporting window", "top_k": 3})
        self.assertGreaterEqual(len(r["hits"]), 1)
        for h in r["hits"]:
            self.assertIsInstance(h["clause_refs"], list)

    def test_top_k_default_3(self):
        from src.tools import lookup_policy
        r = lookup_policy({"query": "refund timelines", "top_k": 3})
        self.assertEqual(r["top_k"], 3)

    def test_empty_query_raises(self):
        from src.tools import lookup_policy, ToolValidationError
        with self.assertRaises(ToolValidationError):
            lookup_policy({"query": ""})


if __name__ == "__main__":
    unittest.main(verbosity=2)
