# ORM — to the point

_Generated 2026-09-06T15:21:25Z from `bytemart_eval.zip`._

`ByteMartDB` is a pure-Python facade over the CSVs in `data/`. It exposes
the 6 lookup methods the agent calls during `action_sequence` execution.

## Class map

```
ByteMartDB                  (facade: load_from_csv, 6 lookup_*, follow_action_sequence, validate)
├─ Customer                 (email, full_name, account_status, no_of_orders)
├─ Product                  (sku, name, category, unit_price, in_stock)
├─ Order                    (order_id, customer_email, items, total_amount, status, …)
├─ OrderItem                (order_id, line_no, sku, qty, unit_price, line_total)
└─ Payment                  (payment_id, customer_email, order_id?, amount, status, txn_id)

Enum types:
├─ OrderStatus              (placed|paid|shipped|delivered|cancelled|refunded|returned)
├─ PaymentMethod            (card|upi|cod)
├─ PaymentStatus            (pending|success|failed|refunded|partially_refunded)
├─ ProductCategory          (gaming_console|gaming_desktop|game_controller|
│                            gaming_keyboard|gaming_mouse|racing_wheel|
│                            vr_headset|monitor|audio|service_fee)
└─ AccountStatus            (active|inactive|suspended)
```

## 6 lookup methods (the 6 tools in `action_sequence`)

| Method | Args | Returns | Used by rows |
|---|---|---|---|
| `lookup_customer(email)` | `email: str` | `Optional[Customer]` | all 36 |
| `lookup_order_by_id(order_id)` | `order_id: str` | `Optional[Order]` | E3, E6, E18, E19, E23, E25, E30 |
| `lookup_order_by_sender_and_product(email, product_hint)` | `email, product_hint` | `Optional[Order]` | E1, E2, E4–E17, E20–E22, ES-032, ES-033, ES-034, ES-035 |
| `lookup_payment_by_transaction_id(txn_id, customer_email)` | `txn_id, customer_email` | `Optional[Payment]` | E28 |
| `lookup_payments_for_order(customer_email, order_id=None, amount=None, status=None)` | varies | `List[Payment]` | E24, E25, E29, E30, ES-034 |
| `lookup_orphan_payment(amount, status)` | `amount, status` | `Optional[Payment]` | E30 |

## `follow_action_sequence(seq)`

Runs an `action_sequence` (list of `{tool, args}` dicts from the eval set)
against the DB and returns the structured data the agent would have access to.

```python
db = ByteMartDB.load_from_csv("data/")
seq = json.loads(eval_row["action_sequence"])
result = db.follow_action_sequence(seq)
# result has keys: customer, order, payments, …  depending on which tools fired
```

## Computed properties (selected)

| Property | Type | Meaning |
|---|---|---|
| `Order.paid_amount` | `Decimal` | Σ successful payments for the order |
| `Order.refunded_amount` | `Decimal` | Σ refunded payments for the order |
| `Order.is_fully_paid` | `bool` | `paid_amount >= total_amount` |
| `Order.is_cancellable` | `bool` | `current_status in {placed, paid}` |
| `Order.is_replacement_eligible` | `bool` | `current_status == delivered` and within 14d |
| `Order.has_express_delivery` | `bool` | line items contain `EXP-DEL-001` |
| `Order.has_cod_charge` | `bool` | line items contain `COD-CHG` |
| `Payment.is_orphan` | `bool` | `status=failed AND order_id IS NULL` |
| `Payment.is_successful` | `bool` | `status=success` |
| `Payment.is_refunded` | `bool` | `status=refunded` |

## Business rules (enforced by `ByteMartDB.validate()`)

1. `total_amount == Σ order_items.line_total` (per order)
2. `no_of_orders` matches the actual count of orders per customer
3. Every successful/refunded payment has a corresponding `order_id`
4. Every `failed` payment has `order_id IS NULL`
5. COD orders with `payment_method=cod` carry exactly one `COD-CHG` line item
6. Express-delivery orders carry exactly one `EXP-DEL-001` line item
7. No duplicate `transaction_id` across distinct (customer, order) pairs

## `get_billing_breakdown(order_id)`

Returns a `BillingBreakdown` (product cost + surcharge line) for the response
composer. Use case: agent says *"Your bill: PS5 Disc @ ₹54,990 + Express Delivery @ ₹100"*.

```python
breakdown = db.get_billing_breakdown("BM200010")
# BillingBreakdown(
#   components=[BillingComponent(product="PS5-DISC-001", amount=54990.00),
#               BillingComponent(product="EXP-DEL-001", amount=100.00, kind="surcharge")],
#   total=55090.00,
#   payment_summary=PaymentRecord(...),
# )
```

## Documented divergences vs `spec.md §5.1`

- Spec names 8 LLM-callable tools (`get_customer_info`, `get_order_info`, `get_product_info`, `get_catalogue`, `lookup_policy`, `check_payment_status`, `request_refund`, `escalate_to_human`).
- Bundle uses 6 `lookup_*` methods (different names, no overlap). Two bundle methods (`lookup_order_by_sender_and_product`, `lookup_orphan_payment`) don't exist in the spec. Three spec tools (`get_product_info`, `get_catalogue`, `lookup_policy`) are never exercised in any `action_sequence`.
- The workflow handles its own tool vocabulary; bundle is the source of truth per project convention.