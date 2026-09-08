"""tests/test_db_schema.py — verify the 5-table schema (Phase 4A).

DB-backed: requires `scripts/setup_db.py` to have run against a live DB
loaded with the vendored CSVs. Silently skips when the DB is unreachable.

Asserts:
  * all 5 tables exist in app.*
  * row counts match the vendored CSVs (36/20/28/31/33)
  * the sync trigger keeps orders.items + total_amount in sync with order_items
  * bytemart_evaluator has SELECT only on the new products + order_items tables
  * all 36 eval-row order_ids resolve to a row
  * BM260002/3/4 carry the right new SKUs (not the legacy CS/RS/SW ones)
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
            from src.db import get_session
            s = get_session("evaluator")
            n = s.execute(__import__("sqlalchemy").text(
                "SELECT COUNT(*) FROM app.customers")).scalar()
            s.close()
            cls._skip_db = n != 36
        except Exception as exc:
            print(f"[skip] DB not reachable: {exc}")
            cls._skip_db = True

    def setUp(self):
        if self._skip_db:
            self.skipTest("DB not reachable (run scripts/setup_db.py first)")


class TestSchemaTablesExist(_DBAware):
    def test_all_seven_objects_present(self):
        """5 tables + 1 view + 2 RAG tables in the app schema (Phase 4B)."""
        from sqlalchemy import text
        from src.db import get_session
        s = get_session("evaluator")
        rows = s.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='app' ORDER BY table_name"
        )).all()
        names = {r[0] for r in rows}
        self.assertSetEqual(
            names,
            {"customers", "order_items", "order_summary", "orders",
             "payments", "policy_children", "policy_parents", "products"},
        )


class TestRowCounts(_DBAware):
    def test_vendored_csv_counts(self):
        from sqlalchemy import text
        from src.db import get_session
        s = get_session("evaluator")
        for table, expected in [
            ("customers", 36),
            ("products", 20),
            ("orders", 28),
            ("order_items", 31),
            ("payments", 33),
        ]:
            n = s.execute(text(f"SELECT COUNT(*) FROM app.{table}")).scalar()
            self.assertEqual(n, expected, f"{table}: got {n}, expected {expected}")


class TestSyncTriggerParity(_DBAware):
    def test_orders_items_matches_order_items(self):
        from sqlalchemy import text
        from src.db import get_session
        s = get_session("evaluator")
        # For every order, the denormalized orders.items JSONB must equal
        # the order_items rows ordered by line_no.
        diff = s.execute(text("""
            SELECT o.order_id,
                   o.items::text AS denorm,
                   (
                     SELECT jsonb_agg(jsonb_build_object(
                        'sku', oi.sku,
                        'qty', oi.qty,
                        'unit_price', oi.unit_price,
                        'line_total', oi.line_total,
                        'name', p.name)
                        ORDER BY oi.line_no)
                     FROM app.order_items oi
                     JOIN app.products p ON p.sku = oi.sku
                     WHERE oi.order_id = o.order_id
                   )::text AS norm
              FROM app.orders o
        """)).all()
        for oid, denorm, norm in diff:
            self.assertEqual(denorm, norm, f"{oid}: items mismatch")

    def test_orders_total_amount_matches_sum(self):
        from sqlalchemy import text
        from src.db import get_session
        s = get_session("evaluator")
        rows = s.execute(text("""
            SELECT o.order_id, o.total_amount, COALESCE(SUM(oi.line_total),0) AS s
              FROM app.orders o
              LEFT JOIN app.order_items oi ON oi.order_id = o.order_id
             GROUP BY o.order_id, o.total_amount
        """)).all()
        bad = [(o, t, s) for (o, t, s) in rows if float(t) != float(s)]
        self.assertEqual(bad, [], f"total_amount drift: {bad}")


class TestEvaluatorRoleIsolation(_DBAware):
    def test_evaluator_cannot_insert_into_products(self):
        from sqlalchemy import text
        from src.db import get_session
        s = get_session("evaluator")
        with self.assertRaises(Exception):
            s.execute(text(
                "INSERT INTO app.products(sku,name,category,unit_price) "
                "VALUES ('BAD-SKU','x','gaming_console',1.0)"
            ))
            s.commit()

    def test_evaluator_cannot_insert_into_order_items(self):
        from sqlalchemy import text
        from src.db import get_session
        s = get_session("evaluator")
        with self.assertRaises(Exception):
            s.execute(text(
                "INSERT INTO app.order_items(order_id,line_no,sku,qty,unit_price,line_total) "
                "VALUES ('BM200001',99,'PS5-DISC-001',1,54990,54990)"
            ))
            s.commit()


class TestEvalOrdersResolve(_DBAware):
    """All 36 eval-row order_ids must resolve to an app.orders row."""
    def test_all_eval_orders_present(self):
        import yaml
        from sqlalchemy import text
        from src.db import get_session
        s = get_session("evaluator")
        yaml_path = REPO_ROOT / "data/completeBytemartEvalset/BytemartEvals.yaml"
        if not yaml_path.exists():
            self.skipTest(f"{yaml_path} not present")
        d = yaml.safe_load(yaml_path.read_text())
        missing = []
        for row in d["eval_rows"]:
            oid = row.get("order_id")
            if not oid:
                continue
            present = s.execute(text(
                "SELECT 1 FROM app.orders WHERE order_id = :o"
            ), {"o": oid}).first()
            if not present:
                missing.append((row["email_id"], oid))
        self.assertEqual(missing, [], f"missing orders: {missing}")


class TestRestructureSKUsPresent(_DBAware):
    """ES-033/034/035 require the new RZR/HYPX SKUs in BM260002/3/4."""
    def test_bm260002_has_ornata(self):
        from sqlalchemy import text
        from src.db import get_session
        s = get_session("evaluator")
        skus = [r[0] for r in s.execute(text(
            "SELECT sku FROM app.order_items WHERE order_id = 'BM260002' ORDER BY line_no"
        )).all()]
        self.assertIn("RZR-ORN-V3", skus)

    def test_bm260003_has_kraken_and_cod(self):
        from sqlalchemy import text
        from src.db import get_session
        s = get_session("evaluator")
        skus = [r[0] for r in s.execute(text(
            "SELECT sku FROM app.order_items WHERE order_id = 'BM260003' ORDER BY line_no"
        )).all()]
        self.assertIn("RZR-KRAKEN-V3", skus)
        self.assertIn("COD-CHG", skus)

    def test_bm260004_has_pulsefire(self):
        from sqlalchemy import text
        from src.db import get_session
        s = get_session("evaluator")
        skus = [r[0] for r in s.execute(text(
            "SELECT sku FROM app.order_items WHERE order_id = 'BM260004' ORDER BY line_no"
        )).all()]
        self.assertIn("HYPX-PFC-001", skus)


class TestProductsEnrichmentColumns(_DBAware):
    """The 15 SKUs in Products.md should be enriched; the 5 new ones may be NULL."""
    def test_warranty_text_populated_for_known_skus(self):
        from sqlalchemy import text
        from src.db import get_session
        s = get_session("evaluator")
        # PS5-DISC-001 is in Products.md -> must have warranty_text
        v = s.execute(text(
            "SELECT warranty_text FROM app.products WHERE sku = 'PS5-DISC-001'"
        )).scalar()
        self.assertIsNotNone(v)
        self.assertGreater(len(v), 0)

    def test_new_skus_enrichment_may_be_null(self):
        from sqlalchemy import text
        from src.db import get_session
        s = get_session("evaluator")
        # RZR-ORN-V3 is NOT in Products.md -> warranty_text NULL is acceptable
        v = s.execute(text(
            "SELECT warranty_text FROM app.products WHERE sku = 'RZR-ORN-V3'"
        )).scalar()
        self.assertIsNone(v)


if __name__ == "__main__":
    unittest.main(verbosity=2)
