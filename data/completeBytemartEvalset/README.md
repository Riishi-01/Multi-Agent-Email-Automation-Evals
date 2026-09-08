# completeBytemartEvalset

Generated **2026-09-06T15:21:25Z** from `bytemart_eval.zip`. Synthetic evaluation dataset for an
agentic email-support workflow over a gaming-ecom retailer.

## Contents

| File | Purpose |
|---|---|
| `BytemartEvals.yaml` | The artifact: 36 eval rows + rubric/tool vocabularies in machine-readable YAML |
| `EVALS.md` | Human-readable per-email narrative: subject, intent, decision, action_sequence, rubric, expected behavior |
| `ORM.md` | To-the-point: `ByteMartDB` facade + 5 model classes + 6 lookup methods + business rules |
| `TABLE.md` | To-the-point: 5 tables + 2 triggers + 1 view, FK graph, CHECK constraints, category enum |
| `DB.md` | Bootstrap: apply `schema.sql` + `seed.sql`, role GRANTs, seed inventory, common queries |

## Reading order

1. `README.md` (this file) — orientation
2. `TABLE.md` — what the data layer looks like
3. `DB.md` — how to bring it up
4. `ORM.md` — how to query it
5. `EVALS.md` — what the agent is being evaluated on
6. `BytemartEvals.yaml` — the structured form for tooling

## Glossary

| Term | Meaning |
|---|---|
| **action_sequence** | Ordered list of tool calls the agent makes to ground its response |
| **auto_send** | Final decision: agent response is sent without human review |
| **escalate** | Final decision: hand the ticket to a human immediately |
| **golden set** | The 36 hand-curated eval cases |
| **hilt** | "Human-in-the-loop" — final decision where a human must approve the agent's draft |
| **hilt_refund** / **hilt_other** | HILT sub-decisions: side-effect producing refund vs other action |
| **identity tier** | One of `verified_customer`, `verified_order`, `mismatch`, `unverified` (per spec §14.3) |
| **PII middleware** | Tokenizes PII on intake; rehydrates args before tool calls; redacts results |
| **rubric** | Per-row list of metric objects the evaluator scores the response against |
| **spec_intent** | 6-value intent enum (`info`, `logistic`, `refund`, `complaint`, `escalation`, `other`) |

## Synthetic data caveats

- **Customer emails with `@test.com`** (the 6 policy-expansion rows + ES-031..ES-036) are explicitly synthetic markers.
- **`synthetic_today = 2026-09-06`** — relative-date logic in the eval set is anchored here.
- **Phone numbers** are not in the bundle (per `PII_PATTERNS` note in spec §6.1 — pincode overlap makes them unsafe to pattern-match).
- **`payment_id` values in `seed.sql` are real `uuid4()`s** generated at build time; `data/payments.csv` mirrors them.
