#!/usr/bin/env python3
"""scripts/run_eval.py — orchestrator: eval_set → score → report.

Usage:
    python scripts/run_eval.py
    python scripts/run_eval.py --eval-set data/completeBytemartEvalset/BytemartEvals.yaml \\
                                --trace-dir data/runs \\
                                --scores-out  data/runs/scores.csv \\
                                --report-out  data/runs/report.md \\
                                --cost-out    data/runs/cost.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.agent.eval_set import run_eval_set, load_eval_set
from src.agent.score import (
    score_traces, write_scores_csv, write_report,
)
from src.agent.judge import JudgeClient
from src.agent.metric_tracker import MetricTracker
from src.agent.logger import install_console, get_logger


DEFAULT_EVAL_SET = REPO_ROOT / "data/completeBytemartEvalset" / "BytemartEvals.yaml"
DEFAULT_TRACE_DIR = REPO_ROOT / "data" / "runs"


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the ByteMart email eval suite.")
    ap.add_argument("--eval-set", default=str(DEFAULT_EVAL_SET),
                    help="Path to BytemartEvals.yaml")
    ap.add_argument("--trace-dir", default=str(DEFAULT_TRACE_DIR),
                    help="Directory to write per-email traces + manifest")
    ap.add_argument("--scores-out", default=None,
                    help="Path to scores.csv (default: <trace_dir>/scores.csv)")
    ap.add_argument("--report-out", default=None,
                    help="Path to report.md (default: <trace_dir>/report.md)")
    ap.add_argument("--cost-out",   default=None,
                    help="Path to cost.json (default: <trace_dir>/cost.json)")
    ap.add_argument("--mapping-csv", default=str(REPO_ROOT / "data" / "bytemart_eval" / "data" / "email_table_mapping.csv"),
                    help="Path to email_table_mapping.csv (read by AM-005 for linked_order_id)")
    ap.add_argument("--no-judge", action="store_true",
                    help="Skip the judge layer even if JUDGE_* env vars are set")
    args = ap.parse_args()

    log = install_console()
    log.info("=== run_eval start ===")

    eval_set_path = Path(args.eval_set)
    trace_dir = Path(args.trace_dir)
    scores_out = Path(args.scores_out) if args.scores_out else (trace_dir / "scores.csv")
    report_out = Path(args.report_out) if args.report_out else (trace_dir / "report.md")
    cost_out   = Path(args.cost_out)   if args.cost_out   else (trace_dir / "cost.json")

    # 1) Run all 36 emails through Workflow.
    log.info("phase 1: run_eval_set (writes traces + manifest + run.jsonl)")
    manifest_path = run_eval_set(eval_set_path, trace_dir=trace_dir)
    manifest = json.loads(manifest_path.read_text())
    log.info("manifest: %d emails, decisions=%s", manifest["n_emails"],
             manifest["decisions"])

    # 2) Load the email_table_mapping.csv for linked_order_id enrichment.
    mapping: dict[str, dict] = {}
    if Path(args.mapping_csv).exists():
        import csv
        with open(args.mapping_csv) as f:
            for row in csv.DictReader(f):
                mapping[row["email_id"]] = row

    # 3) Pair every trace with its golden row.
    log.info("phase 2: scoring")
    golden_rows = {r["email_id"]: r for r in load_eval_set(eval_set_path)}
    eid_order = list(golden_rows.keys())
    pairs: list[tuple[dict, dict]] = []
    for eid in eid_order:
        trace_path = trace_dir / f"{eid}.json"
        if not trace_path.exists():
            log.warning("missing trace for %s; skipping", eid)
            continue
        trace = json.loads(trace_path.read_text())
        golden = dict(golden_rows[eid])  # copy
        # Layer in the mapping
        m = mapping.get(eid, {})
        if m.get("linked_order_id"):
            golden["linked_order_id"] = m["linked_order_id"]
        pairs.append((trace, golden))

    # 4) Wire judge if configured.
    judge = None
    if not args.no_judge:
        judge = JudgeClient()  # picks up JUDGE_* env
        if judge.is_configured():
            from src.agent.metric_tracker import detect_provider, has_pricing
            provider = detect_provider(judge.model, judge.base_url)
            priced   = has_pricing(judge.model)
            log.info("judge layer: enabled (provider=%s, model=%s, base_url=%s, "
                     "bundled_pricing=%s)",
                     provider, judge.model, judge.base_url, priced)
        else:
            log.info("judge layer: disabled (JUDGE_* env not fully set)")
            judge = None

    # 5) Score.
    tracker = MetricTracker(out_path=cost_out)
    scores = score_traces(pairs, judge=judge, eval_set_path=eval_set_path,
                          run_judge=judge is not None)
    # Record per-judge-call cost events.
    if judge is not None and judge.is_configured():
        for s in scores:
            for m in s.metrics:
                if m.layer == "judge" and (m.tokens_in or m.tokens_out):
                    tracker.record(
                        email_id=s.email_id,
                        model=m.judge_model or judge.model,
                        tokens_in=m.tokens_in, tokens_out=m.tokens_out,
                        duration_ms=m.duration_ms,
                        metric_id=m.metric_id,
                    )
    write_scores_csv(scores, scores_out)
    write_report(scores, report_out, manifest=manifest)
    tracker.finalize()

    avg = sum(s.weighted_score for s in scores) / max(1, len(scores))
    log.info("scored %d emails -> avg weighted score: %.4f", len(scores), avg)
    log.info("outputs:")
    log.info("  manifest: %s", manifest_path)
    log.info("  scores:   %s", scores_out)
    log.info("  report:   %s", report_out)
    log.info("  cost:     %s", cost_out)
    log.info("=== run_eval done ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
