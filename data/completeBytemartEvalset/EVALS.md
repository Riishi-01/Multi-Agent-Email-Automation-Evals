# EVALS — 36 eval emails (full coverage)

_Generated 2026-09-06T15:21:25Z from `bytemart_eval.zip`._

## §1 Decision distribution

| Decision | Count | Description |
|---|---:|---|
| `auto_send` | 23 | Verifier passed; agent response sent without human review |
| `hilt_other` | 7 | HILT for non-refund side-effect (cancel / address change / replacement) |
| `hilt_refund` | 4 | HILT for a refund — human must approve the money movement |
| `escalate` | 2 | Hand off entirely (DPDPA / NCH / data erasure) |

## §2 Action-sequence chains (7 patterns)

| Chain | Count | Rows |
|---|---:|---|
| `lookup_customer → lookup_order_by_sender_and_product` | 22 | E1, E2, E4, E5, E7, E8, E9, E10, E11, E12, E13, E14, E15, E16, E17, E20, E21, E22, ES-032, ES-033, ES-035, ES-036 |
| `lookup_customer → lookup_order_by_id` | 5 | E3, E6, E18, E19, E23 |
| `lookup_customer → lookup_order_by_sender_and_product → lookup_payments_for_order` | 3 | E24, E29, ES-034 |
| `lookup_customer` | 3 | E26, E27, ES-031 |
| `lookup_customer → lookup_order_by_id → lookup_payments_for_order` | 1 | E25 |
| `lookup_customer → lookup_payment_by_transaction_id` | 1 | E28 |
| `lookup_customer → lookup_order_by_id → lookup_payments_for_order → lookup_orphan_payment` | 1 | E30 |

## §3 Rubric vocabulary (49 `metric_id`s)

| Metric ID | Type | Used by (rows) |
|---|---|---|
| `acknowledges_manufacture_date` |  | 1 rows |
| `acknowledges_right_to_erasure` |  | 1 rows |
| `addresses_birthday_deadline` |  | 1 rows |
| `answers_compatibility_grounded` |  | 4 rows |
| `answers_dual_device_grounded` |  | 1 rows |
| `answers_grounded` |  | 1 rows |
| `answers_upi_to_agent` |  | 1 rows |
| `avoids_template_dismissal` |  | 1 rows |
| `cites_Rs100_not_full_amount` |  | 1 rows |
| `cites_Rs54990_amount` |  | 2 rows |
| `cites_Rs5499_not_Rs54990` |  | 1 rows |
| `cites_both_Rs100_and_Rs24999` |  | 1 rows |
| `cites_cpa_2019_section_35` |  | 1 rows |
| `cites_policy_clause` |  | 36 rows |
| `clarity_structure` |  | 36 rows |
| `completeness` |  | 36 rows |
| `declines_firmly_politely` |  | 2 rows |
| `declines_premature_refund` |  | 1 rows |
| `explains_cod_surcharge` |  | 1 rows |
| `explains_failure_status` |  | 1 rows |
| `explains_force_majeure_tiers` |  | 1 rows |
| `grounded_in_product_knowledge` |  | 5 rows |
| `immediate_escalation` |  | 1 rows |
| `matches_register` |  | 34 rows |
| `mentions_14d_window` |  | 6 rows |
| `mentions_48h_acknowledgment` |  | 1 rows |
| `mentions_cable_swap_diagnostic` |  | 1 rows |
| `mentions_express_surcharge` |  | 1 rows |
| `mentions_grievance_officer` |  | 1 rows |
| `mentions_initiated_2_days_ago` |  | 2 rows |
| `mentions_keeping_original` |  | 1 rows |
| `mentions_no_price_match_policy` |  | 1 rows |
| `no_fabricated_amounts` |  | 36 rows |
| `no_pii_echo` |  | 36 rows |
| `no_refund_promise_during_force_majeure` |  | 1 rows |
| `not_over_promising` |  | 1 rows |
| `offers_box_only_replacement` |  | 1 rows |
| `offers_status_check` |  | 1 rows |
| `offers_troubleshooting_steps` |  | 2 rows |
| `provides_tracking_id` |  | 1 rows |
| `reassures_auto_reversal` |  | 1 rows |
| `requests_photo_evidence` |  | 1 rows |
| `routes_to_DPB` |  | 1 rows |
| `states_bank_credit_window` |  | 1 rows |
| `states_expected_timeline` |  | 1 rows |
| `states_payment_methods_supported` |  | 1 rows |
| `states_refund_initiated` |  | 1 rows |
| `states_refund_status` |  | 1 rows |
| `tone_professional` |  | 36 rows |

**Universal (present on every non-escalate row):**
`tone_professional`, `clarity_structure`, `completeness`, `no_pii_echo`, `no_fabricated_amounts`, `cites_policy_clause`.

**Register-aware:** `matches_register` is added unless `decision=escalate`.

## §4 All 36 eval rows (grouped by decision)

### `auto_send` (23 rows)

#### `E1` — PS5 Delivery Before Sep 1
- sender: `sanya.delhi@gmail.com`
- spec_intent: `logistic` / customer_intent: `delivery_date`
- priority: `low`, register: `formal`, length: `brief`
- order_id: `BM200001`  product: `PlayStation 5 Disc Edition Console`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `["shipping"]`
- row-specific rubric metrics: `['addresses_birthday_deadline']`

#### `E2` — Xbox Series S No Update 15d
- sender: `deepak.lucknow@gmail.com`
- spec_intent: `logistic` / customer_intent: `tracking_status`
- priority: `medium`, register: `formal`, length: `moderate`
- order_id: `BM200002`  product: `Xbox Series S 512GB Console`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `["shipping"]`
- row-specific rubric metrics: `['provides_tracking_id']`

#### `E4` — Express Delivery 3 Days
- sender: `pooja.delhi.arora@gmail.com`
- spec_intent: `logistic` / customer_intent: `express_delivery_request`
- priority: `low`, register: `formal`, length: `brief`
- order_id: `BM200003`  product: `PlayStation 5 Digital Edition Console`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `["shipping"]`
- row-specific rubric metrics: `['mentions_express_surcharge']`

#### `E5` — Transit Insurance
- sender: `harsh.jaipur@gmail.com`
- spec_intent: `info` / customer_intent: `policy_question`
- priority: `low`, register: `formal`, length: `brief`
- order_id: `BM200004`  product: `ByteMart Custom Gaming PC RTX 4070`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `["shipping"]`
- row-specific rubric metrics: `['answers_grounded']`

#### `E7` — PS5 + G923 Compatibility
- sender: `random.inquirer1@gmail.com`
- spec_intent: `info` / customer_intent: `product_compatibility`
- priority: `low`, register: `casual`, length: `brief`
- order_id: `—`  product: `—`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `[]`
- row-specific rubric metrics: `['answers_compatibility_grounded']`

#### `E8` — G PRO on PS3
- sender: `random.inquirer2@gmail.com`
- spec_intent: `info` / customer_intent: `product_compatibility`
- priority: `low`, register: `formal`, length: `brief`
- order_id: `—`  product: `—`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `[]`
- row-specific rubric metrics: `['answers_compatibility_grounded']`

#### `E9` — Xbox + G PRO Power
- sender: `random.inquirer3@gmail.com`
- spec_intent: `info` / customer_intent: `product_compatibility`
- priority: `low`, register: `neutral`, length: `brief`
- order_id: `—`  product: `—`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `[]`
- row-specific rubric metrics: `['answers_compatibility_grounded']`

#### `E10` — G PRO PS5 Troubleshooting
- sender: `mohit.surat@gmail.com`
- spec_intent: `info` / customer_intent: `troubleshooting_help`
- priority: `low`, register: `casual`, length: `brief`
- order_id: `BM200005`  product: `Logitech G PRO Racing Wheel and Pedals`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `[]`
- row-specific rubric metrics: `['offers_troubleshooting_steps']`

#### `E11` — Samsung Odyssey G6 Dual
- sender: `random.inquirer4@gmail.com`
- spec_intent: `info` / customer_intent: `product_feature`
- priority: `low`, register: `formal`, length: `brief`
- order_id: `—`  product: `—`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `[]`
- row-specific rubric metrics: `['answers_dual_device_grounded']`

#### `E12` — Meta Quest 3 512GB
- sender: `ananya.vadodara@gmail.com`
- spec_intent: `info` / customer_intent: `product_feature`
- priority: `low`, register: `casual`, length: `brief`
- order_id: `BM200006`  product: `Meta Quest 3 512GB VR Headset`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `[]`
- row-specific rubric metrics: `['grounded_in_product_knowledge']`

#### `E13` — Steam Deck OLED Software
- sender: `random.inquirer5@gmail.com`
- spec_intent: `info` / customer_intent: `product_question`
- priority: `low`, register: `neutral`, length: `brief`
- order_id: `—`  product: `—`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `[]`
- row-specific rubric metrics: `['grounded_in_product_knowledge']`

#### `E14` — Xbox Controller PS5+Xbox
- sender: `random.inquirer6@gmail.com`
- spec_intent: `info` / customer_intent: `product_compatibility`
- priority: `low`, register: `formal`, length: `brief`
- order_id: `—`  product: `—`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `[]`
- row-specific rubric metrics: `['answers_compatibility_grounded']`

#### `E15` — ASUS ROG Swift KVM
- sender: `random.inquirer7@gmail.com`
- spec_intent: `info` / customer_intent: `product_feature`
- priority: `low`, register: `casual`, length: `brief`
- order_id: `—`  product: `—`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `[]`
- row-specific rubric metrics: `['grounded_in_product_knowledge']`

#### `E16` — 4K OLED Monitor PS5
- sender: `random.inquirer8@gmail.com`
- spec_intent: `info` / customer_intent: `product_question`
- priority: `low`, register: `neutral`, length: `brief`
- order_id: `—`  product: `—`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `[]`
- row-specific rubric metrics: `['grounded_in_product_knowledge']`

#### `E17` — LG UltraGear Aspect Ratio
- sender: `random.inquirer9@gmail.com`
- spec_intent: `info` / customer_intent: `product_feature`
- priority: `low`, register: `casual`, length: `brief`
- order_id: `—`  product: `—`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `[]`
- row-specific rubric metrics: `['grounded_in_product_knowledge']`

#### `E25` — Refund Status
- sender: `divya.nair.kol@gmail.com`
- spec_intent: `refund` / customer_intent: `refund_status`
- priority: `high`, register: `casual`, length: `moderate`
- order_id: `BM293482`  product: `PlayStation 5 Disc Edition Console`
- chain: `lookup_customer → lookup_order_by_id → lookup_payments_for_order`
- policy_ref: `["refund"]`
- row-specific rubric metrics: `['states_refund_status', 'states_expected_timeline', 'mentions_initiated_2_days_ago', 'cites_Rs54990_amount']`

#### `E26` — Crypto Nintendo Switch
- sender: `crypto.buyer@gmail.com`
- spec_intent: `info` / customer_intent: `payment_method`
- priority: `low`, register: `casual`, length: `brief`
- order_id: `—`  product: `—`
- chain: `lookup_customer`
- policy_ref: `["payment"]`
- row-specific rubric metrics: `['states_payment_methods_supported']`

#### `E27` — COD + UPI
- sender: `cod.user@gmail.com`
- spec_intent: `info` / customer_intent: `payment_method`
- priority: `low`, register: `formal`, length: `brief`
- order_id: `—`  product: `—`
- chain: `lookup_customer`
- policy_ref: `["payment"]`
- row-specific rubric metrics: `['explains_cod_surcharge', 'answers_upi_to_agent']`

#### `E28` — Failed Samsung Payment
- sender: `neha.kulkarni.pun@gmail.com`
- spec_intent: `refund` / customer_intent: `refund_failed_payment`
- priority: `medium`, register: `neutral`, length: `moderate`
- order_id: `—`  product: `—`
- chain: `lookup_customer → lookup_payment_by_transaction_id`
- policy_ref: `["payment"]`
- row-specific rubric metrics: `['explains_failure_status', 'reassures_auto_reversal']`

#### `E29` — Refund Cancelled Order
- sender: `aditya.rao.ahm@gmail.com`
- spec_intent: `refund` / customer_intent: `refund_status`
- priority: `medium`, register: `casual`, length: `moderate`
- order_id: `BM200011`  product: `PlayStation 5 Disc Edition Console`
- chain: `lookup_customer → lookup_order_by_sender_and_product → lookup_payments_for_order`
- policy_ref: `["refund"]`
- row-specific rubric metrics: `['states_refund_initiated', 'states_bank_credit_window', 'mentions_initiated_2_days_ago', 'cites_Rs54990_amount']`

#### `ES-032` — Force Majeure Delayed Shipment
- sender: `rohit.m@test.com`
- spec_intent: `logistic` / customer_intent: `force_majeure_refund`
- priority: `medium`, register: `neutral`, length: `detailed`
- order_id: `BM260001`  product: `Wireless Earbuds Pro`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `["shipping"]`
- row-specific rubric metrics: `['offers_status_check', 'declines_premature_refund', 'no_refund_promise_during_force_majeure', 'explains_force_majeure_tiers']`

#### `ES-033` — Return Missing Photo Evidence
- sender: `priya.k@test.com`
- spec_intent: `complaint` / customer_intent: `return_missing_evidence`
- priority: `medium`, register: `neutral`, length: `moderate`
- order_id: `BM260002`  product: `Razer Ornata V3 Gaming Keyboard`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `["returns"]`
- row-specific rubric metrics: `['requests_photo_evidence', 'mentions_14d_window']`

#### `ES-035` — Price-Match / Flash Sale
- sender: `neha.s@test.com`
- spec_intent: `other` / customer_intent: `price_match_request`
- priority: `low`, register: `neutral`, length: `brief`
- order_id: `BM260004`  product: `HyperX Pulsefire Core Gaming Mouse`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `["pricing"]`
- row-specific rubric metrics: `['mentions_no_price_match_policy', 'declines_firmly_politely']`

### `hilt_other` (7 rows)

#### `E3` — Cancel Order
- sender: `amit.verma.delhi@gmail.com`
- spec_intent: `logistic` / customer_intent: `cancellation_request`
- priority: `high`, register: `formal`, length: `moderate`
- order_id: `BM123244`  product: `Xbox Series X 1TB Console`
- chain: `lookup_customer → lookup_order_by_id`
- policy_ref: `["cancellation"]`
- row-specific rubric metrics: `['declines_firmly_politely']`

#### `E6` — Change Address
- sender: `sneha.mumbai86@gmail.com`
- spec_intent: `logistic` / customer_intent: `address_change_request`
- priority: `high`, register: `formal`, length: `moderate`
- order_id: `BM183923`  product: `PlayStation 5 Disc Edition Console`
- chain: `lookup_customer → lookup_order_by_id`
- policy_ref: `["shipping"]`
- row-specific rubric metrics: `['not_over_promising']`

#### `E18` — Damaged PS5 Box
- sender: `rishit1@gmail.com`
- spec_intent: `complaint` / customer_intent: `replacement_damaged_packaging`
- priority: `medium`, register: `casual`, length: `detailed`
- order_id: `BM728349`  product: `PlayStation 5 Disc Edition Console`
- chain: `lookup_customer → lookup_order_by_id`
- policy_ref: `["returns"]`
- row-specific rubric metrics: `['mentions_14d_window', 'offers_box_only_replacement', 'mentions_keeping_original']`

#### `E19` — Xbox Not Turning On
- sender: `priya.kapoor.bng@gmail.com`
- spec_intent: `complaint` / customer_intent: `replacement_defective`
- priority: `high`, register: `casual`, length: `detailed`
- order_id: `BM834853`  product: `Xbox Series S 512GB Console`
- chain: `lookup_customer → lookup_order_by_id`
- policy_ref: `["returns"]`
- row-specific rubric metrics: `['mentions_14d_window', 'mentions_cable_swap_diagnostic', 'offers_troubleshooting_steps']`

#### `E20` — Used Xbox Controller
- sender: `rahulmehta88@yahoo.com`
- spec_intent: `complaint` / customer_intent: `replacement_used_item`
- priority: `medium`, register: `casual`, length: `detailed`
- order_id: `BM200007`  product: `Xbox Wireless Controller - Black`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `["returns"]`
- row-specific rubric metrics: `['mentions_14d_window']`

#### `E21` — Missing HDMI Dell Alienware
- sender: `karan.bhatia.chn@outlook.com`
- spec_intent: `complaint` / customer_intent: `missing_accessory`
- priority: `medium`, register: `casual`, length: `detailed`
- order_id: `BM200008`  product: `Dell Alienware AW3423DWF 34 inch OLED Monitor`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `["returns"]`
- row-specific rubric metrics: `['mentions_14d_window']`

#### `E23` — Wrong Xbox Color
- sender: `vikram.reddy.pun@gmail.com`
- spec_intent: `complaint` / customer_intent: `replacement_wrong_item`
- priority: `medium`, register: `casual`, length: `detailed`
- order_id: `BM932412`  product: `Xbox Wireless Controller - Black`
- chain: `lookup_customer → lookup_order_by_id`
- policy_ref: `["returns"]`
- row-specific rubric metrics: `['mentions_14d_window']`

### `hilt_refund` (4 rows)

#### `E22` — Old Stock Razer BlackShark
- sender: `anjalisingh.hyd@gmail.com`
- spec_intent: `refund` / customer_intent: `compensation_request`
- priority: `medium`, register: `casual`, length: `moderate`
- order_id: `BM200009`  product: `Razer BlackShark V2 Pro Wireless Headset`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `["returns"]`
- row-specific rubric metrics: `['acknowledges_manufacture_date']`

#### `E24` — Refund Fast Delivery
- sender: `rohan.delhi.21@gmail.com`
- spec_intent: `refund` / customer_intent: `refund_surcharge`
- priority: `medium`, register: `neutral`, length: `moderate`
- order_id: `BM200010`  product: `PlayStation 5 Disc Edition Console (+ Express Delivery Surcharge)`
- chain: `lookup_customer → lookup_order_by_sender_and_product → lookup_payments_for_order`
- policy_ref: `["shipping","refund"]`
- row-specific rubric metrics: `['cites_Rs100_not_full_amount']`

#### `E30` — Duplicate Payment
- sender: `manish.jain.mum@gmail.com`
- spec_intent: `refund` / customer_intent: `refund_duplicate`
- priority: `high`, register: `formal`, length: `moderate`
- order_id: `BM189438`  product: `Xbox Wireless Controller - Black`
- chain: `lookup_customer → lookup_order_by_id → lookup_payments_for_order → lookup_orphan_payment`
- policy_ref: `["payment","refund"]`
- row-specific rubric metrics: `['cites_Rs5499_not_Rs54990']`

#### `ES-034` — COD Fee Reconciliation
- sender: `amit.v@test.com`
- spec_intent: `refund` / customer_intent: `refund_surcharge`
- priority: `medium`, register: `neutral`, length: `moderate`
- order_id: `BM260003`  product: `Razer Kraken V3 HyperSense Gaming Headset`
- chain: `lookup_customer → lookup_order_by_sender_and_product → lookup_payments_for_order`
- policy_ref: `["cancellation","refund"]`
- row-specific rubric metrics: `['cites_both_Rs100_and_Rs24999']`

### `escalate` (2 rows)

#### `ES-031` — Data Erasure DPDPA
- sender: `user.privacy@test.com`
- spec_intent: `escalation` / customer_intent: `data_erasure`
- priority: `critical`, register: `neutral`, length: `moderate`
- order_id: `—`  product: `—`
- chain: `lookup_customer`
- policy_ref: `["privacy","external"]`
- row-specific rubric metrics: `['acknowledges_right_to_erasure', 'routes_to_DPB']`

#### `ES-036` — Consumer Forum / NCH Escalation
- sender: `legal.user@test.com`
- spec_intent: `escalation` / customer_intent: `legal_escalation`
- priority: `critical`, register: `neutral`, length: `detailed`
- order_id: `BM260005`  product: `LED Monitor 27-inch`
- chain: `lookup_customer → lookup_order_by_sender_and_product`
- policy_ref: `["grievance","external"]`
- row-specific rubric metrics: `['immediate_escalation', 'mentions_grievance_officer', 'mentions_48h_acknowledgment', 'avoids_template_dismissal', 'cites_cpa_2019_section_35']`

## §5 Policy-expansion deep-dive (ES-031..ES-036)

These 6 rows exercise policy areas the base 30 don't cover. Each uses
an explicit `@test.com` customer for synthetic isolation.

### `ES-031` — Data Erasure DPDPA
- sender: `user.privacy@test.com`
- order_id: ``  product: ``
- decision: `escalate`  policy_ref: `["privacy","external"]`
- subject: *Data Erasure DPDPA*
- body: _I want to delete my account and all associated personal data from your systems immediately._

### `ES-032` — Force Majeure Delayed Shipment
- sender: `rohit.m@test.com`
- order_id: `BM260001`  product: `Wireless Earbuds Pro`
- decision: `auto_send`  policy_ref: `["shipping"]`
- subject: *Force Majeure Delayed Shipment*
- body: _My order has been stuck at the hub for 10 days due to local strikes. I want a refund now._

### `ES-033` — Return Missing Photo Evidence
- sender: `priya.k@test.com`
- order_id: `BM260002`  product: `Razer Ornata V3 Gaming Keyboard`
- decision: `auto_send`  policy_ref: `["returns"]`
- subject: *Return Missing Photo Evidence*
- body: _Returning this gaming keyboard because one of the keys is stuck and unresponsive. Here is the original packaging._

### `ES-034` — COD Fee Reconciliation
- sender: `amit.v@test.com`
- order_id: `BM260003`  product: `Razer Kraken V3 HyperSense Gaming Headset`
- decision: `hilt_refund`  policy_ref: `["cancellation","refund"]`
- subject: *COD Fee Reconciliation*
- body: _I cancelled my order while it was out for delivery. Why is my Rs.100 COD fee not refunded?_

### `ES-035` — Price-Match / Flash Sale
- sender: `neha.s@test.com`
- order_id: `BM260004`  product: `HyperX Pulsefire Core Gaming Mouse`
- decision: `auto_send`  policy_ref: `["pricing"]`
- subject: *Price-Match / Flash Sale*
- body: _The item I bought yesterday is now 20% cheaper on your site. Please refund the difference._

### `ES-036` — Consumer Forum / NCH Escalation
- sender: `legal.user@test.com`
- order_id: `BM260005`  product: `LED Monitor 27-inch`
- decision: `escalate`  policy_ref: `["grievance","external"]`
- subject: *Consumer Forum / NCH Escalation*
- body: _Unresolved issue for 40 days. Escalating to National Consumer Helpline and e-Daakhil._
