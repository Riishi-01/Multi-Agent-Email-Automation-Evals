-- =====================================================================
-- ByteMart v4 — Postgres DDL (canonical, idempotent)
--
-- Source: docs/build.yaml#phase-4-data-layer
-- Apply order:
--   1. CREATE EXTENSIONS (pgcrypto + vector)
--   2. CREATE SCHEMA app
--   3. CREATE ROLES (bytemart_owner, bytemart_evaluator)
--   4. CREATE TABLES in app (customers, products, orders, order_items, payments, policy_embeddings)
--   5. CREATE INDEXES
--   6. CREATE TRIGGERS (orders -> customers.no_of_orders; order_items -> orders cache)
--   7. CREATE VIEW order_summary
--   8. GRANTs (evaluator gets SELECT only)
--
-- Connections:
--   - Apply as the owner (setup_db.py uses the owner role).
--   - The agent runtime connects as bytemart_evaluator (read-only).
-- =====================================================================

-- 1. Extensions -------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Schema ----------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION CURRENT_USER;

-- 3. Roles ------------------------------------------------------------------
-- (Owner password is whatever the container's POSTGRES_PASSWORD is.)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bytemart_owner') THEN
    EXECUTE format(
      'CREATE ROLE bytemart_owner LOGIN PASSWORD %L SUPERUSER',
      current_setting('server_version_num')
    );
    -- The above won't work cleanly; instead, the docker-compose container
    -- creates bytemart_owner as the bootstrap superuser. We only need to
    -- promote it to SUPERUSER if it isn't already. Skip if not present.
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bytemart_evaluator') THEN
    CREATE ROLE bytemart_evaluator LOGIN PASSWORD 'bytemart_evaluator_pw';
  END IF;
EXCEPTION WHEN others THEN
  -- bootstrap superuser already exists with POSTGRES_USER; ignore.
  NULL;
END
$$;

ALTER ROLE bytemart_owner SET search_path = app, public;

-- 4. Tables -----------------------------------------------------------------
SET search_path TO app;

-- app.customers -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.customers (
  email           text PRIMARY KEY,
  full_name       text NOT NULL,
  account_status  text NOT NULL DEFAULT 'active'
                   CHECK (account_status IN ('active','inactive','suspended')),
  no_of_orders    int  NOT NULL DEFAULT 0,
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- app.products (Phase 4A) ---------------------------------------------------
-- 20 SKUs (gaming_console, gaming_desktop, game_controller, gaming_keyboard,
-- gaming_mouse, racing_wheel, vr_headset, monitor, audio, service_fee).
-- products.csv is canonical; Products.md fills nullable enrichment columns.
CREATE TABLE IF NOT EXISTS app.products (
  sku               text PRIMARY KEY
                     CHECK (sku ~ '^[A-Z0-9-]{3,16}$'),
  name              text NOT NULL,
  category          text NOT NULL
                     CHECK (category IN (
                       'gaming_console','gaming_desktop','game_controller',
                       'gaming_keyboard','gaming_mouse','racing_wheel',
                       'vr_headset','monitor','audio','service_fee'
                     )),
  unit_price        numeric(10,2) NOT NULL CHECK (unit_price > 0),
  in_stock          boolean NOT NULL DEFAULT true,
  description       text,
  -- Products.md enrichment (nullable; only 15 SKUs are in Products.md).
  warranty_text     text,
  tech_description  text,
  tech_details      text,
  stock_count       int
);

-- app.orders ----------------------------------------------------------------
-- items (jsonb) is a denormalized cache, kept in sync with app.order_items
-- by trg_sync_order_items_cache. CSV load provides the initial items + total;
-- the trigger is the source of truth thereafter.
CREATE TABLE IF NOT EXISTS app.orders (
  order_id              text PRIMARY KEY
                         CHECK (order_id ~ '^[A-Za-z]{2}[0-9]{6}$'),
  customer_email        text NOT NULL REFERENCES app.customers(email),
  items                 jsonb NOT NULL,
  total_amount          numeric(10,2) NOT NULL CHECK (total_amount > 0),
  payment_method        text NOT NULL
                         CHECK (payment_method IN ('card','upi','cod')),
  order_date            timestamptz NOT NULL DEFAULT now(),
  expected_delivery     date,
  current_status        text NOT NULL DEFAULT 'placed'
                         CHECK (current_status IN ('placed','paid','shipped','delivered',
                                                    'cancelled','refunded','returned')),
  shipping_address      jsonb NOT NULL,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS orders_order_id_lower_uniq
  ON app.orders (lower(order_id));
CREATE INDEX IF NOT EXISTS orders_customer_email_idx ON app.orders (customer_email);
CREATE INDEX IF NOT EXISTS orders_current_status_idx ON app.orders (current_status);

-- app.order_items (Phase 4A) -----------------------------------------------
CREATE TABLE IF NOT EXISTS app.order_items (
  order_id      text NOT NULL REFERENCES app.orders(order_id) ON DELETE CASCADE,
  line_no       int  NOT NULL CHECK (line_no > 0),
  sku           text NOT NULL REFERENCES app.products(sku),
  qty           int  NOT NULL CHECK (qty > 0),
  unit_price    numeric(10,2) NOT NULL CHECK (unit_price > 0),
  line_total    numeric(10,2) NOT NULL CHECK (line_total > 0),
  PRIMARY KEY (order_id, line_no),
  -- Invariant: line_total must equal qty * unit_price (no rounding tricks).
  CHECK (line_total = qty * unit_price)
);

CREATE INDEX IF NOT EXISTS order_items_sku_idx    ON app.order_items (sku);
CREATE INDEX IF NOT EXISTS order_items_order_idx  ON app.order_items (order_id);

-- app.payments --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.payments (
  payment_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_email    text NOT NULL REFERENCES app.customers(email),
  order_id          text REFERENCES app.orders(order_id),
  amount            numeric(10,2) NOT NULL,
  method            text NOT NULL CHECK (method IN ('card','upi','cod')),
  status            text NOT NULL CHECK (status IN
                       ('pending','success','failed','refunded','partially_refunded')),
  transaction_id    text,
  failure_reason    text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CHECK (
    (status IN ('success','refunded','partially_refunded') AND order_id IS NOT NULL)
    OR (status = 'failed' AND order_id IS NULL)
    OR (status = 'pending')
  )
);

CREATE INDEX IF NOT EXISTS payments_customer_status_idx ON app.payments (customer_email, status);
CREATE INDEX IF NOT EXISTS payments_order_idx           ON app.payments (order_id);
CREATE INDEX IF NOT EXISTS payments_transaction_idx     ON app.payments (transaction_id);

-- app.policy_parents (Phase 4B — RAG source-of-truth containers) ----------
-- One row per ~1200-token parent. No embedding; the parent text is the
-- citation context returned by lookup_policy(...).
CREATE TABLE IF NOT EXISTS app.policy_parents (
  parent_id      text PRIMARY KEY,
  doc_id         text NOT NULL,
  version        text NOT NULL,
  page_from      int  NOT NULL,
  page_to        int  NOT NULL,
  -- Phase 5G.5: line range for Markdown-sourced parents (0 for PDF).
  line_from      int  NOT NULL DEFAULT 0,
  line_to        int  NOT NULL DEFAULT 0,
  -- Phase 5G.5: clause anchor (e.g. "5", "5.1") for clause-split parents.
  clause_anchor  text NOT NULL DEFAULT '',
  text           text NOT NULL,
  token_count    int  NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS policy_parents_doc_idx ON app.policy_parents (doc_id);

-- Phase 5G.5 migration: add line range + clause anchor columns to existing tables.
ALTER TABLE app.policy_parents
  ADD COLUMN IF NOT EXISTS line_from     int  NOT NULL DEFAULT 0;
ALTER TABLE app.policy_parents
  ADD COLUMN IF NOT EXISTS line_to       int  NOT NULL DEFAULT 0;
ALTER TABLE app.policy_parents
  ADD COLUMN IF NOT EXISTS clause_anchor text NOT NULL DEFAULT '';

-- app.policy_children (Phase 4B — RAG embedding units) --------------------
-- Children are ~300-token overlapping slices of a parent. ivfflat over
-- cosine distance. top_k=3 in lookup_policy matches on CHILDREN, then
-- dedupes to the parent text for the citation context.
CREATE TABLE IF NOT EXISTS app.policy_children (
  chunk_id     text PRIMARY KEY,
  parent_id    text NOT NULL REFERENCES app.policy_parents(parent_id) ON DELETE CASCADE,
  doc_id       text NOT NULL,
  version      text NOT NULL,
  chunk_seq    int  NOT NULL,
  text         text NOT NULL,
  token_count  int  NOT NULL,
  embedding    public.vector(1536) NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (parent_id, chunk_seq)
);

CREATE INDEX IF NOT EXISTS policy_children_parent_idx ON app.policy_children (parent_id);
CREATE INDEX IF NOT EXISTS policy_children_doc_idx    ON app.policy_children (doc_id);

-- ivfflat index is built AFTER ingest (pgvector requires data + ANALYZE).

-- 5. Trigger: maintain customers.no_of_orders on orders insert/delete ------
CREATE OR REPLACE FUNCTION app.fn_update_customer_order_count() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    UPDATE app.customers SET no_of_orders = no_of_orders + 1
      WHERE email = NEW.customer_email;
  ELSIF TG_OP = 'DELETE' THEN
    UPDATE app.customers SET no_of_orders = no_of_orders - 1
      WHERE email = OLD.customer_email;
  END IF;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_order_count ON app.orders;
CREATE TRIGGER trg_order_count
  AFTER INSERT OR DELETE ON app.orders
  FOR EACH ROW EXECUTE FUNCTION app.fn_update_customer_order_count();

-- 5b. Trigger: sync orders.items (jsonb) + total_amount from order_items ---
-- AFTER INSERT/UPDATE/DELETE on order_items, rebuild the denormalized cache
-- so it always matches the normalized row set.
CREATE OR REPLACE FUNCTION app.fn_sync_order_items_cache() RETURNS trigger AS $$
DECLARE
  v_order_id text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    v_order_id := OLD.order_id;
  ELSE
    v_order_id := NEW.order_id;
  END IF;

  UPDATE app.orders o
     SET items = (
           SELECT COALESCE(jsonb_agg(
             jsonb_build_object(
               'sku',        oi.sku,
               'qty',        oi.qty,
               'unit_price', oi.unit_price,
               'line_total', oi.line_total,
               'name',       p.name
             ) ORDER BY oi.line_no
           ), '[]'::jsonb)
             FROM app.order_items oi
             JOIN app.products    p ON p.sku = oi.sku
            WHERE oi.order_id = o.order_id
         ),
         total_amount = COALESCE((
           SELECT SUM(oi.line_total)
             FROM app.order_items oi
            WHERE oi.order_id = o.order_id
         ), 0),
         updated_at = now()
   WHERE o.order_id = v_order_id;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sync_order_items_cache ON app.order_items;
CREATE TRIGGER trg_sync_order_items_cache
  AFTER INSERT OR UPDATE OR DELETE ON app.order_items
  FOR EACH ROW EXECUTE FUNCTION app.fn_sync_order_items_cache();

-- 6. View: order_summary (Phase 4A; eval inspection) ------------------------
DROP VIEW IF EXISTS app.order_summary;
CREATE VIEW app.order_summary AS
SELECT
  o.order_id,
  o.customer_email,
  c.full_name,
  o.current_status,
  o.total_amount,
  COALESCE(p.paid_amount, 0)        AS paid_amount,
  COALESCE(p.refunded_amount, 0)    AS refunded_amount,
  jsonb_array_length(o.items)       AS line_count,
  o.order_date,
  o.expected_delivery
FROM app.orders o
JOIN app.customers c ON c.email = o.customer_email
LEFT JOIN (
  SELECT order_id,
         SUM(amount) FILTER (WHERE status='success') AS paid_amount,
         SUM(amount) FILTER (WHERE status='refunded') AS refunded_amount
    FROM app.payments
   WHERE order_id IS NOT NULL
   GROUP BY order_id
) p ON p.order_id = o.order_id;

-- 7. GRANTs -----------------------------------------------------------------
GRANT ALL ON SCHEMA app TO bytemart_owner;
GRANT ALL ON ALL TABLES IN SCHEMA app TO bytemart_owner;
GRANT ALL ON ALL SEQUENCES IN SCHEMA app TO bytemart_owner;
ALTER DEFAULT PRIVILEGES IN SCHEMA app GRANT ALL ON TABLES TO bytemart_owner;

GRANT USAGE ON SCHEMA app TO bytemart_evaluator;
GRANT SELECT ON ALL TABLES IN SCHEMA app TO bytemart_evaluator;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA app TO bytemart_evaluator;
ALTER DEFAULT PRIVILEGES IN SCHEMA app GRANT SELECT ON TABLES TO bytemart_evaluator;
