# DB — bootstrap, seed inventory, common queries

_Generated 2026-09-06T15:21:25Z from `bytemart_eval.zip`._

## Bootstrap

```bash
# 1. Apply the schema (5 tables + 2 triggers + 1 view)
psql -d bytemart -f schema.sql

# 2. Apply the seed (36 customers, 20 products, 28 orders, 31 order_items, 33 payments)
psql -d bytemart -f seed.sql

# 3. Apply role-based GRANTs (see spec §4.4)
#    - agent_user : PII-redacted SELECT on customers/orders/products/policies/
#                  tickets/ticket_messages; INSERT on tickets/hilt_training;
#                  no GRANT on refunds/ticket_token_maps
#    - dashboard_user : full SELECT/INSERT/UPDATE/DELETE on all tables
```

## Seed inventory

- **36** customers (`sanya.delhi@gmail.com` and 35 more)
- **20** products (10 categories, ₹100–₹1,29,990 range)
- **28** orders (24 placed/paid/shipped, 2 cancelled, 2 refunded, 2 returned, …)
- **31** order_items (3 multi-line orders: BM200007, BM200010, BM260003)
- **33** payments (success + refunded + 2 orphan failed)

## Synthetic anchor

- `synthetic_today = 2026-09-06` — eval rows use relative dates like "yesterday", "10 days ago".
- Orders span 2024-04 through 2025-08; the 6 policy-expansion orders (BM260001..BM260005) are 2025-08.

## Common queries

```sql
-- Q0: identity lookup (always first; every email)
SELECT email, full_name, account_status, no_of_orders
  FROM customers WHERE email = $1;

-- Q1: order lookup by order_id (with payment status join)
SELECT o.order_id, o.customer_email, o.current_status, o.total_amount,
       o.expected_delivery, o.shipping_address,
       p.status AS payment_status, p.transaction_id
  FROM orders o
  JOIN payments p ON p.order_id = o.order_id
 WHERE o.order_id = $1
   AND o.customer_email = $2;

-- Q2: order lookup by sender + product hint (no order_id in email)
SELECT order_id, current_status, total_amount, expected_delivery, order_date,
       items
  FROM orders
 WHERE customer_email = $1
   AND items->0->>'name' ILIKE '%' || $2 || '%'
 ORDER BY order_date DESC
 LIMIT 1;

-- Q4: payments for an order (refund-status investigations)
SELECT payment_id, order_id, amount, status, transaction_id, failure_reason
  FROM payments
 WHERE customer_email = $1
   AND order_id       = $2;

-- Q5: orphan failed payment (no order_id; used for duplicate-payment case)
SELECT payment_id, order_id, amount, status, transaction_id, failure_reason
  FROM payments
 WHERE customer_email = $1
   AND amount = $2
   AND status = 'failed'
   AND order_id IS NULL;
```

## Role-based access (spec §4.4)

| Table | `agent_user` | `dashboard_user` |
|---|---|---|
| `customers, orders, payments, products, policies` | SELECT | full |
| `tickets, ticket_messages` | SELECT, INSERT, UPDATE | full |
| `hilt_tickets` | SELECT, INSERT (no UPDATE) | full |
| `refunds` | **revoked entirely** | full |
| `ticket_token_maps` | **revoked entirely** | full |
| `eval.hilt_training, eval.runs` | SELECT, INSERT | full |