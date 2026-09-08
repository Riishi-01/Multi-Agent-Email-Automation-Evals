# EvalTestLogs

This directory holds the **historical trace logs** from running the email automation pipeline against the held-out 36-email eval set, plus the LLM-as-judge output that scores each trace.

> **Eval set is held-out.** The 36 emails in `data/bytemart_eval/data/emails.tsv` are the gold standard; this directory is the result of running the pipeline against them. Treat every file here as an artifact, not as a test fixture.

## Layout

```
EvalTestLogs/
├── workflow_runs/<run_id>/
│   ├── manifest.json                # run metadata: parallelism, decision counts, n_emails
│   ├── workflow.yaml                # YAML form of the eval config used
│   └── <email_id>.json              # per-email trace (schema_version="1.2")
│
├── judge_runs/<run_id>/
│   ├── manifest.json                # judge run metadata: pass/fail/unscored counts, total_cost_usd
│   ├── judge.yaml                   # judge rubric + provider config snapshot
│   └── metric_costs.json            # token/cost ledger
│
├── manual_review/<run_id>/
│   └── <email_id>.json              # hand-curated audit traces (e.g. E26 series)
│
├── phase1.log                       # phase-1 data-layer build log
└── phase2.log                       # phase-2 agents/tools build log
```

## How traces are produced

```bash
# 1. Run the workflow over the 36-email eval set
python scripts/run_eval_parallel.py \
    --emails data/bytemart_eval/data/emails.tsv \
    --workers 5 \
    --out EvalTestLogs/workflow_runs/<run_id>/

# 2. Run the LLM-as-judge over the workflow traces
python scripts/run_eval_parallel.py \
    --judge \
    --workflow-runs EvalTestLogs/workflow_runs/<run_id>/ \
    --out EvalTestLogs/judge_runs/<run_id>/
```

## Trace schema (1.2)

Each `<email_id>.json` contains:

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `"1.2"` | Bumped when fields are added |
| `email_id` | `str` | matches `emails.tsv` row id |
| `started_at` / `finished_at` | `ISO 8601` | |
| `retriever.tool_calls[]` | `list[dict]` | each call has `tool`, `args`, `result`, `duration_ms` |
| `retriever.policies[]` | `list[PolicyHit]` | `{doc_id, similarity, clause_anchor, text}` |
| `resolver.action` | `str` | one of `auto_send`, `hilt_refund`, `hilt_other`, `escalate` |
| `resolver.intent` | `str` | one of `info`, `request_refund`, `request_action`, `complaint`, `grievance` |
| `resolver.draft` | `str` | full draft body (may be empty for `hilt_*`) |
| `resolver.hilt_reason` | `dict` | 7-field reason for `hilt_*` decisions |
| `reflexive.dimension_scores` | `dict[str, float]` | 5 dims, 0..1 each |
| `reflexive.confidence` | `float` | min of the 5 dims |
| `reflexive.verdict` | `str` | `accept`, `regenerate`, or `escalate` |
| `outcome` | `str` | `sent`, `pending`, or `human_queue` |
| `error` | `str \| None` | populated on tool/LLM failures |

## Adding an email

1. Append a row to `data/bytemart_eval/data/emails.tsv` (id must be unique)
2. Append a row to `data/bytemart_eval/data/eval_golden_set.csv` (intent, decision, cited policy)
3. Re-run the workflow + judge — a new trace appears in `EvalTestLogs/`

The pipeline is idempotent per `(email_id, run_id)` pair.
