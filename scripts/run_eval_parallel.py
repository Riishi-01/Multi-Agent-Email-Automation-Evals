#!/usr/bin/env python3
"""scripts/run_eval_parallel.py — 5-worker parallel eval orchestrator.

Two phases (one CLI script, two subcommands):
  phase1: run the workflow (retriever + resolver + reflexive) on every
           email in the eval set, in parallel. Writes:
             data/runs/workflow_runs/<run_id>/<email_id>.json
             data/runs/workflow_runs/<run_id>/workflow.yaml
             data/runs/workflow_runs/<run_id>/manifest.json
  phase2: judge the most recent workflow run (or --phase1-run-id).
           Writes:
             data/runs/judge_runs/<judge_run_id>/judge.yaml
             data/runs/judge_runs/<judge_run_id>/manifest.json
  full:   both phases in sequence.

Usage:
  python scripts/run_eval_parallel.py phase1 \
    --eval-set data/completeBytemartEvalset/BytemartEvals.yaml \
    --out-dir data/runs/workflow_runs/ --parallel 5

  python scripts/run_eval_parallel.py phase2 \
    --trace-dir data/runs/workflow_runs/ --parallel 5

  python scripts/run_eval_parallel.py full \
    --eval-set data/completeBytemartEvalset/BytemartEvals.yaml \
    --out-dir data/runs/ --parallel 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="5-worker parallel eval runner (Phase 5F).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    # phase1
    p1 = sub.add_parser("phase1", help="run the workflow in parallel")
    p1.add_argument("--eval-set", required=True,
                    help="Path to BytemartEvals.yaml")
    p1.add_argument("--out-dir", default="data/runs/workflow_runs",
                    help="Output directory for workflow_runs/<run_id>/")
    p1.add_argument("--parallel", type=int, default=5)
    p1.add_argument("--notes", default="", help="Free-form run notes")

    # phase2
    p2 = sub.add_parser("phase2", help="judge the most recent workflow run")
    p2.add_argument("--trace-dir", default="data/runs/workflow_runs",
                    help="Directory containing workflow run subdirs")
    p2.add_argument("--out-dir", default="data/runs/judge_runs",
                    help="Output directory for judge_runs/<judge_run_id>/")
    p2.add_argument("--parallel", type=int, default=5)
    p2.add_argument("--notes", default="", help="Free-form run notes")
    p2.add_argument("--phase1-run-id", default=None,
                    help="If set, judge this specific run instead of the latest")

    # full
    p3 = sub.add_parser("full", help="phase1 + phase2")
    p3.add_argument("--eval-set", required=True)
    p3.add_argument("--out-dir", default="data/runs",
                    help="Output base dir; workflow_runs/ and judge_runs/ are created inside")
    p3.add_argument("--parallel", type=int, default=5)
    p3.add_argument("--notes", default="", help="Free-form run notes")

    args = ap.parse_args()

    from src.agent.parallel_eval import (
        run_workflow_phase, run_judge_phase,
    )

    if args.cmd == "phase1":
        run_id = run_workflow_phase(
            args.eval_set, args.out_dir,
            parallel=args.parallel, notes=args.notes,
        )
        print(f"phase1 done: run_id={run_id}")
        return 0

    if args.cmd == "phase2":
        judge_run_id = run_judge_phase(
            args.trace_dir, args.out_dir,
            parallel=args.parallel, notes=args.notes,
            phase1_run_id=args.phase1_run_id,
        )
        print(f"phase2 done: judge_run_id={judge_run_id}")
        return 0

    if args.cmd == "full":
        wf_dir = Path(args.out_dir) / "workflow_runs"
        jd_dir = Path(args.out_dir) / "judge_runs"
        run_id = run_workflow_phase(
            args.eval_set, wf_dir,
            parallel=args.parallel, notes=args.notes,
        )
        print(f"phase1 done: run_id={run_id}")
        judge_run_id = run_judge_phase(
            wf_dir, jd_dir,
            parallel=args.parallel, notes=args.notes,
            phase1_run_id=run_id,
        )
        print(f"phase2 done: judge_run_id={judge_run_id}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
