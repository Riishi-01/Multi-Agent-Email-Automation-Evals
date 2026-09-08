# ByteMart Email-Evals — Run Report

- Eval set: `?`
- N emails: 5
- Total time: 100 ms

## Decision distribution — 4-way (bundle sub-action)

| decision | actual | expected |
|---|---|---|
| auto_send | 2 | 2 |
| hilt_refund | 3 | 2 |
| hilt_other | 0 | 0 |
| escalate | 0 | 1 |

## Decision distribution — 3-way (workflow outcome category)

| category | actual | expected |
|---|---|---|
| auto_send | 2 | 2 |
| hilt | 3 | 2 |
| escalate | 0 | 1 |

## Per-metric pass rates

| metric | layer | pass | fail | unscored |
|---|---|---|---|---|
| cites_policy_clause | field | 0 | 5 | 0 |
| correct_action | deterministic | 4 | 1 | 0 |
| correct_action_category | deterministic | 4 | 1 | 0 |
| intent_correct | deterministic | 3 | 2 | 0 |
| linked_order_resolved | deterministic | 5 | 0 | 0 |
| no_fabricated_amounts | field | 3 | 2 | 0 |
| no_pii_echo | field | 5 | 0 | 0 |
| no_unnecessary_calls | deterministic | 5 | 0 | 0 |
| right_tools_called | deterministic | 4 | 1 | 0 |


## Avg weighted score: 0.7333
