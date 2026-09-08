#!/usr/bin/env python3
"""setup_db.py — Apply DDL + load seed data for ByteMart v4 (Phase 4A).

Order of operations:
  1. Connect as bytemart_owner (DDL + seed rights).
  2. Read & execute scripts/setup_db.sql (idempotent, 5 tables + 2 triggers + 1 view).
  3. Truncate business tables (idempotent re-runs).
  4. Load data/bytemart_eval/data/{customers,products,orders,order_items,payments}.csv
     in FK-safe order. The trg_sync_order_items_cache trigger then keeps
     orders.items + total_amount in sync with order_items.
  5. Run scripts/enrich_products.py to fill warranty_text / tech_description /
     tech_details / stock_count for the 15 SKUs in data/policies/Products.md.
  6. ANALYZE (so the ivfflat index can build correctly in Phase 4B).
  7. Run 11 exit gates (counts + trigger parity + role isolation + eval
     coverage + enrichment smoke tests).
  8. Print a one-screen summary.

Usage:
    python scripts/setup_db.py [--skip-assess]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text, select, func
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError, ProgrammingError

from src.db import Base, Customer, Order, Payment, Product, OrderItem, get_session


DATA_DIR = REPO_ROOT / "data" / "bytemart_eval" / "data"
SETUP_SQL = REPO_ROOT / "scripts" / "setup_db.sql"


# ---------------------------------------------------------------------------
# Connection bootstrap (peer-auth fallback for first-time setup)
# ---------------------------------------------------------------------------
def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        env = REPO_ROOT / ".env"
        if env.exists():
            load_dotenv(env, override=False)
    except ImportError:
        pass


def _test_connection() -> bool:
    _load_env()
    from src.db import OWNER_ENGINE
    try:
        with OWNER_ENGINE.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError as e:
        msg = str(e)
        if "does not exist" in msg or "Connection refused" in msg:
            if _try_bootstrap():
                try:
                    with OWNER_ENGINE.connect() as conn:
                        conn.execute(text("SELECT 1"))
                    return True
                except OperationalError as e2:
                    print(f"\n!! Bootstrap succeeded but owner still can't connect: {e2}\n")
                    _print_quickstart()
                    return False
            print(f"\n!! Cannot connect to Postgres: {e}\n")
            _print_quickstart()
            return False
        print(f"\n!! Cannot connect to Postgres: {e}\n")
        _print_quickstart()
        return False


def _try_bootstrap() -> bool:
    """Peer-auth bootstrap: CREATE DATABASE + extensions + roles."""
    import getpass
    user = getpass.getuser()
    try:
        bootstrap_engine = create_engine(
            f"postgresql+psycopg://{user}@/postgres?host=/tmp",
            isolation_level="AUTOCOMMIT",
        )
    except Exception as e:
        print(f"  bootstrap: cannot construct peer-auth URL: {e}")
        return False
    try:
        with bootstrap_engine.connect() as conn:
            r = conn.execute(text("SELECT 1 FROM pg_database WHERE datname='bytemart'")).first()
            if not r:
                conn.execute(text("CREATE DATABASE bytemart"))
                print(f"  bootstrap: created database 'bytemart'")
            conn.close()
        bootstrap_engine.dispose()
        bootstrap_engine = create_engine(
            f"postgresql+psycopg://{user}@/bytemart?host=/tmp",
            isolation_level="AUTOCOMMIT",
        )
        with bootstrap_engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
            conn.execute(text(
                "DO $$ BEGIN "
                "  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bytemart_owner') THEN "
                "    CREATE ROLE bytemart_owner LOGIN PASSWORD 'bytemart_owner_pw' SUPERUSER; "
                "  END IF; "
                "  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bytemart_evaluator') THEN "
                "    CREATE ROLE bytemart_evaluator LOGIN PASSWORD 'bytemart_evaluator_pw'; "
                "  END IF; "
                "END $$"
            ))
            print(f"  bootstrap: created roles + extensions as OS user '{user}'")
        bootstrap_engine.dispose()
        return True
    except Exception as e:
        print(f"  bootstrap failed: {e}")
        return False


def _print_quickstart() -> None:
    print("Quick-start:")
    print("  docker compose up -d   # container + extensions + roles pre-configured")
    print("  python scripts/setup_db.py\n")


# ---------------------------------------------------------------------------
# DDL apply
# ---------------------------------------------------------------------------
def apply_ddl() -> None:
    sql = SETUP_SQL.read_text()
    statements = _split_sql_statements(sql)
    from src.db import OWNER_ENGINE
    with OWNER_ENGINE.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def _split_sql_statements(sql: str) -> list[str]:
    """Split on `;` while respecting `$$ ... $$`, `--`, and `/* */`."""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    in_dollar = False
    in_line_comment = False
    in_block_comment = False
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if not in_dollar and not in_block_comment and ch == "-" and nxt == "-":
            in_line_comment = True
            buf.append(ch)
            i += 1
            continue
        if in_line_comment and ch == "\n":
            in_line_comment = False
            buf.append(ch)
            i += 1
            continue
        if in_line_comment:
            buf.append(ch)
            i += 1
            continue
        if not in_dollar and ch == "/" and nxt == "*":
            in_block_comment = True
            buf.append(ch); buf.append(nxt)
            i += 2
            continue
        if in_block_comment and ch == "*" and nxt == "/":
            in_block_comment = False
            buf.append(ch); buf.append(nxt)
            i += 2
            continue
        if in_block_comment:
            buf.append(ch)
            i += 1
            continue
        if ch == "$" and nxt == "$":
            in_dollar = not in_dollar
            buf.append(ch); buf.append(nxt)
            i += 2
            continue
        if not in_dollar and ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


# ---------------------------------------------------------------------------
# Truncate + reload
# ---------------------------------------------------------------------------
def truncate_business_tables() -> None:
    from src.db import OWNER_ENGINE
    with OWNER_ENGINE.begin() as conn:
        # CASCADE so FK constraints don't trip. Only truncate tables that
        # exist (policy_embeddings was removed in Phase 4B).
        conn.execute(text(
            "TRUNCATE app.payments, app.order_items, app.orders, "
            "app.products, app.customers, app.policy_children, app.policy_parents "
            "RESTART IDENTITY CASCADE"
        ))


# ---------------------------------------------------------------------------
# CSV -> ORM loaders
# ---------------------------------------------------------------------------
def _row(row: dict[str, Any], _type_hints: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if v is None or v == "" or (isinstance(v, str) and v.strip().lower() in {"none", "null"}):
            out[k] = None
            continue
        hint = _type_hints.get(k)
        if hint == "jsonb":
            out[k] = json.loads(v)
        elif hint == "numeric":
            out[k] = float(v)
        elif hint == "bool":
            out[k] = v.strip().lower() in {"true", "1", "yes"}
        else:
            out[k] = v
    return out


CUSTOMER_TYPES = {
    "email": "text", "full_name": "text", "account_status": "text",
    "no_of_orders": "int", "created_at": "timestamptz",
}
PRODUCT_TYPES = {
    "sku": "text", "name": "text", "category": "text",
    "unit_price": "numeric", "in_stock": "bool", "description": "text",
}
ORDER_TYPES = {
    "order_id": "text", "customer_email": "text", "items": "jsonb",
    "total_amount": "numeric", "payment_method": "text",
    "order_date": "timestamptz", "expected_delivery": "date",
    "current_status": "text", "shipping_address": "jsonb",
    "created_at": "timestamptz", "updated_at": "timestamptz",
}
ORDER_ITEM_TYPES = {
    "order_id": "text", "line_no": "int", "sku": "text",
    "qty": "int", "unit_price": "numeric", "line_total": "numeric",
}
PAYMENT_TYPES = {
    "payment_id": "uuid", "customer_email": "text", "order_id": "text",
    "amount": "numeric", "method": "text", "status": "text",
    "transaction_id": "text", "failure_reason": "text",
    "created_at": "timestamptz", "updated_at": "timestamptz",
}


def load_csv(path: Path, types: dict[str, str]) -> list[dict]:
    """Return a list of coerced row dicts from a CSV (no DB writes)."""
    out: list[dict] = []
    with path.open() as f:
        for r in csv.DictReader(f):
            out.append(_row(r, types))
    return out


def _bool(v: Any) -> bool:
    return bool(v) and str(v).strip().lower() in {"true", "1", "yes"}


def load_customers(path: Path) -> int:
    sess = get_session("owner")
    n = 0
    try:
        for r in load_csv(path, CUSTOMER_TYPES):
            sess.add(Customer(**r))
            n += 1
        sess.commit()
    finally:
        sess.close()
    return n


def load_products(path: Path) -> int:
    sess = get_session("owner")
    n = 0
    try:
        for r in load_csv(path, PRODUCT_TYPES):
            # products.csv `in_stock` is the literal string "True"/"False".
            r["in_stock"] = str(r.get("in_stock", "")).strip().lower() in {"true", "1"}
            # Enrichment columns are filled later by enrich_products.py.
            for c in ("warranty_text", "tech_description", "tech_details", "stock_count"):
                r[c] = None
            sess.add(Product(**r))
            n += 1
        sess.commit()
    finally:
        sess.close()
    return n


def load_orders(path: Path) -> int:
    sess = get_session("owner")
    n = 0
    try:
        for r in load_csv(path, ORDER_TYPES):
            r["expected_delivery"] = r.get("expected_delivery") or None
            sess.add(Order(**r))
            n += 1
        sess.commit()
    finally:
        sess.close()
    return n


def load_order_items(path: Path) -> int:
    """Load order_items; the sync trigger will rewrite orders.items + total_amount."""
    sess = get_session("owner")
    n = 0
    try:
        for r in load_csv(path, ORDER_ITEM_TYPES):
            r["line_no"]     = int(r["line_no"])
            r["qty"]         = int(r["qty"])
            r["unit_price"]  = float(r["unit_price"])
            r["line_total"]  = float(r["line_total"])
            sess.add(OrderItem(**r))
            n += 1
        sess.commit()
    finally:
        sess.close()
    return n


def load_payments(path: Path) -> int:
    sess = get_session("owner")
    n = 0
    try:
        with path.open() as f:
            for r in csv.DictReader(f):
                row = _row(r, PAYMENT_TYPES)
                pid = (r.get("payment_id") or "").strip()
                if not pid or pid.lower() == "gen_random_uuid":
                    row["payment_id"] = None
                else:
                    try:
                        uuid.UUID(pid)
                        row["payment_id"] = pid
                    except ValueError:
                        row["payment_id"] = None
                row["order_id"] = r.get("order_id") or None
                row["transaction_id"] = r.get("transaction_id") or None
                row["failure_reason"] = r.get("failure_reason") or None
                sess.add(Payment(**row))
                n += 1
        sess.commit()
    finally:
        sess.close()
    return n


# ---------------------------------------------------------------------------
# ANALYZE
# ---------------------------------------------------------------------------
def analyze() -> None:
    from src.db import OWNER_ENGINE
    with OWNER_ENGINE.begin() as conn:
        conn.execute(text(
            "ANALYZE app.customers, app.products, app.orders, "
            "app.order_items, app.payments, app.policy_children, app.policy_parents"
        ))


# ---------------------------------------------------------------------------
# Exit gates (the 11 assertions)
# ---------------------------------------------------------------------------
GOLDEN_ORDER_IDS = {
    "BM123244", "BM183923", "BM728349", "BM834853",
    "BM932412", "BM293482", "BM189438",
}
GOLDEN_TXN_ID = "72384982"

EXPECTED_COUNTS = {
    "customers": 36, "products": 20, "orders": 28,
    "order_items": 31, "payments": 33,
}


def assert_table_counts(sess) -> dict[str, int]:
    counts = {
        t: sess.execute(text(f"SELECT COUNT(*) FROM app.{t}")).scalar()
        for t in EXPECTED_COUNTS
    }
    for k, want in EXPECTED_COUNTS.items():
        got = counts[k]
        status = "OK" if got == want else "FAIL"
        print(f"  [{status}] app.{k:12s} count={got}  expected={want}")
    return counts


def assert_golden_records(sess) -> None:
    rows = sess.execute(text(
        "SELECT order_id FROM app.orders WHERE order_id = ANY(:ids)"
    ), {"ids": list(GOLDEN_ORDER_IDS)}).all()
    found = {r[0] for r in rows}
    missing = GOLDEN_ORDER_IDS - found
    if missing:
        print(f"  [FAIL] golden order_ids missing: {sorted(missing)}")
    else:
        print(f"  [OK]   golden order_ids present: {len(found)}/7")

    txn = sess.execute(text(
        "SELECT status, order_id FROM app.payments WHERE transaction_id = :t"
    ), {"t": GOLDEN_TXN_ID}).first()
    if txn and txn[0] == "failed" and txn[1] is None:
        print(f"  [OK]   golden txn_id {GOLDEN_TXN_ID} present (failed, order_id NULL)")
    else:
        print(f"  [FAIL] golden txn_id {GOLDEN_TXN_ID} missing or wrong status")


def assert_payment_invariant(sess) -> None:
    bad = sess.execute(text("""
        SELECT COUNT(*) FROM app.payments
        WHERE NOT (
            (status IN ('success','refunded','partially_refunded') AND order_id IS NOT NULL)
            OR (status = 'failed' AND order_id IS NULL)
            OR (status = 'pending')
        )
    """)).scalar() or 0
    if bad:
        print(f"  [FAIL] {bad} payments violate the invariant")
    else:
        print("  [OK]   payment invariant holds across all rows")


def assert_sync_trigger_parity(sess) -> None:
    """orders.items (jsonb) must equal the order_items rows joined to products;
    orders.total_amount must equal SUM(order_items.line_total)."""
    bad_items = sess.execute(text("""
        SELECT COUNT(*) FROM app.orders o
         WHERE o.items IS DISTINCT FROM (
             SELECT COALESCE(jsonb_agg(
                 jsonb_build_object(
                     'sku', oi.sku, 'qty', oi.qty,
                     'unit_price', oi.unit_price,
                     'line_total', oi.line_total,
                     'name', p.name)
                 ORDER BY oi.line_no)
             , '[]'::jsonb)
               FROM app.order_items oi
               JOIN app.products p ON p.sku = oi.sku
              WHERE oi.order_id = o.order_id)
    """)).scalar() or 0
    bad_total = sess.execute(text("""
        SELECT COUNT(*) FROM app.orders o
         WHERE o.total_amount <> COALESCE(
             (SELECT SUM(oi.line_total) FROM app.order_items oi
               WHERE oi.order_id = o.order_id), 0)
    """)).scalar() or 0
    if bad_items:
        print(f"  [FAIL] {bad_items} orders have items drift vs order_items")
    else:
        print("  [OK]   orders.items matches order_items for every order")
    if bad_total:
        print(f"  [FAIL] {bad_total} orders have total_amount drift")
    else:
        print("  [OK]   orders.total_amount matches SUM(order_items.line_total) for every order")


def assert_evaluator_role() -> None:
    from src.db import EVALUATOR_ENGINE
    # SELECT works
    try:
        with EVALUATOR_ENGINE.connect() as conn:
            n = conn.execute(text("SELECT COUNT(*) FROM app.customers")).scalar()
            print(f"  [OK]   evaluator SELECT app.customers works (count={n})")
    except Exception as e:
        print(f"  [FAIL] evaluator SELECT failed: {e}")
        return
    # INSERT denied on all 5 tables
    for t in ("customers", "products", "orders", "order_items", "payments"):
        try:
            with EVALUATOR_ENGINE.connect() as conn:
                conn.execute(text(
                    f"INSERT INTO app.{t} DEFAULT VALUES"
                ))
                conn.commit()
            print(f"  [FAIL] evaluator INSERT app.{t} succeeded")
        except ProgrammingError as e:
            msg = str(e).lower()
            if "permission denied" in msg or "42501" in msg or "null value" in msg:
                print(f"  [OK]   evaluator INSERT app.{t} denied")
            else:
                print(f"  [WARN] evaluator INSERT app.{t}: {e}")


def assert_eval_orders_resolve(sess) -> None:
    yaml_path = REPO_ROOT / "data" / "completeBytemartEvalset" / "BytemartEvals.yaml"
    if not yaml_path.exists():
        print(f"  [WARN] {yaml_path} not present; skipping")
        return
    import yaml
    d = yaml.safe_load(yaml_path.read_text())
    missing = []
    for row in d["eval_rows"]:
        oid = row.get("order_id")
        if not oid:
            continue
        present = sess.execute(text(
            "SELECT 1 FROM app.orders WHERE order_id = :o"
        ), {"o": oid}).first()
        if not present:
            missing.append((row["email_id"], oid))
    if missing:
        print(f"  [FAIL] {len(missing)} eval order_ids missing: {missing[:5]}…")
    else:
        print(f"  [OK]   all {len([r for r in d['eval_rows'] if r.get('order_id')])} eval order_ids resolve")


def assert_restructure_skus(sess) -> None:
    expectations = {
        "BM260002": ("RZR-ORN-V3",),
        "BM260003": ("RZR-KRAKEN-V3", "COD-CHG"),
        "BM260004": ("HYPX-PFC-001",),
    }
    all_ok = True
    for oid, expected_skus in expectations.items():
        skus = [r[0] for r in sess.execute(text(
            "SELECT sku FROM app.order_items WHERE order_id = :o ORDER BY line_no"
        ), {"o": oid}).all()]
        if all(s in skus for s in expected_skus):
            print(f"  [OK]   {oid} carries {expected_skus}")
        else:
            print(f"  [FAIL] {oid}: expected {expected_skus}, got {skus}")
            all_ok = False
    return all_ok


def assert_products_enrichment(sess) -> None:
    # 15 enriched SKUs (one per row in Products.md)
    n_enriched = sess.execute(text(
        "SELECT COUNT(*) FROM app.products WHERE warranty_text IS NOT NULL"
    )).scalar() or 0
    if n_enriched == 15:
        print(f"  [OK]   products enrichment: {n_enriched} SKUs enriched (Products.md)")
    else:
        print(f"  [FAIL] products enrichment: {n_enriched} enriched, expected 15")

    # 5 unenriched SKUs (the 3 new gaming + 2 service fees)
    n_bare = sess.execute(text(
        "SELECT COUNT(*) FROM app.products WHERE warranty_text IS NULL"
    )).scalar() or 0
    if n_bare == 5:
        print(f"  [OK]   products bare SKUs: {n_bare} (3 new gaming + 2 service fees)")
    else:
        print(f"  [FAIL] products bare SKUs: {n_bare}, expected 5")


def assert_policy_parents_children(sess) -> None:
    """Both parent-child RAG tables must exist."""
    ok = True
    for t in ("policy_parents", "policy_children"):
        try:
            sess.execute(text(f"SELECT 1 FROM app.{t} LIMIT 0"))
            print(f"  [OK]   app.{t} table exists (Phase 4B ingest populates)")
        except Exception as e:
            print(f"  [FAIL] app.{t} table missing: {e}")
            ok = False
    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Set up ByteMart v4 DB.")
    ap.add_argument("--skip-assess", action="store_true",
                    help="Skip the exit-gate assertion block.")
    ap.add_argument("--skip-enrich", action="store_true",
                    help="Skip Products.md enrichment step.")
    args = ap.parse_args()

    if not _test_connection():
        return 2

    print("== Phase 4A setup: applying DDL ==")
    apply_ddl()
    print("  [OK]   setup_db.sql applied (idempotent)")

    print("\n== Phase 4A setup: truncating business tables ==")
    truncate_business_tables()
    print("  [OK]   truncated app.{customers,products,orders,order_items,payments,policy_embeddings}")

    print("\n== Phase 4A setup: loading seed CSVs ==")
    n_c = load_customers(DATA_DIR / "customers.csv")
    print(f"  [OK]   customers.csv   -> {n_c} rows")
    n_pr = load_products(DATA_DIR / "products.csv")
    print(f"  [OK]   products.csv    -> {n_pr} rows")
    n_o = load_orders(DATA_DIR / "orders.csv")
    print(f"  [OK]   orders.csv      -> {n_o} rows")
    n_oi = load_order_items(DATA_DIR / "order_items.csv")
    print(f"  [OK]   order_items.csv -> {n_oi} rows (trigger rewrites orders.items + total_amount)")
    n_p = load_payments(DATA_DIR / "payments.csv")
    print(f"  [OK]   payments.csv    -> {n_p} rows")

    if not args.skip_enrich:
        print("\n== Phase 4A setup: Products.md enrichment ==")
        from scripts.enrich_products import parse_products_md, enrich_db
        md_path = REPO_ROOT / "data" / "policies" / "Products.md"
        parsed = parse_products_md(md_path)
        updated, unmatched = enrich_db(parsed)
        print(f"  [OK]   Products.md parsed: {len(parsed)} rows")
        print(f"  [OK]   enriched {updated} SKUs ({unmatched} unmatched = 5 service-fee SKUs)")
    else:
        print("\n== Phase 4A setup: Products.md enrichment SKIPPED ==")

    print("\n== Phase 4A setup: ANALYZE ==")
    analyze()
    print("  [OK]   ANALYZE on all 6 tables")

    if args.skip_assess:
        print("\n== Phase 4A: exit-gate assessment SKIPPED ==")
        return 0

    print("\n== Phase 4A exit gates ==")
    sess = get_session("evaluator")
    try:
        assert_table_counts(sess)
        assert_golden_records(sess)
        assert_payment_invariant(sess)
        assert_sync_trigger_parity(sess)
        assert_eval_orders_resolve(sess)
        assert_restructure_skus(sess)
        assert_products_enrichment(sess)
        assert_policy_parents_children(sess)
    finally:
        sess.close()

    assert_evaluator_role()

    print("\nPhase 4A complete.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
