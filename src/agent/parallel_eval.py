"""src/agent/parallel_eval.py — 5-worker parallel eval runner.

Three entry points:
  1. run_workflow_phase(eval_set_path, run_dir, parallel=5)
     — phase 1: workflow run (retriever + resolver + reflexive),
       writes workflow YAML + per-email trace files.
  2. run_judge_phase(trace_dir, run_dir, parallel=5)
     — phase 2: judge the most recent workflow run, writes
       judge YAML with pass/fail per metric.
  3. redact_pii(trace) — PII redaction for the workflow YAML output
     (the trace file on disk stays complete).

The two YAMLs are written by write_workflow_yaml() and write_judge_yaml().
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from .judge import JudgeClient
from .eval_set import load_eval_set, normalize_eval_row
from .metric_tracker import MetricTracker
from .types import Email, Trace
from .workflow import Workflow


log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# PII redaction
# ---------------------------------------------------------------------------
# Email pattern (RFC 5322 simplified)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+\.[A-Za-z0-9.\-]+\b")
# Phone pattern: 10+ digits with optional separators
_PHONE_RE = re.compile(r"\+?\d[\d\-\s]{8,}\d")
# Indian PIN code (6 digits)
_PINCODE_RE = re.compile(r"\b\d{6}\b")


def redact_email(value: Any) -> Any:
    """Replace email addresses in a string with <email>."""
    if not isinstance(value, str):
        return value
    return _EMAIL_RE.sub("<email>", value)


def redact_phone(value: Any) -> Any:
    """Replace phone-like digit runs in a string with <phone>."""
    if not isinstance(value, str):
        return value
    return _PHONE_RE.sub("<phone>", value)


def redact_string(value: str) -> str:
    """Apply email + phone redaction to a string."""
    if not isinstance(value, str):
        return value
    out = _EMAIL_RE.sub("<email>", value)
    out = _PHONE_RE.sub("<phone>", out)
    return out


def redact_dict(d: Any) -> Any:
    """Recursively redact a dict / list / scalar structure."""
    if isinstance(d, dict):
        return {k: redact_dict(v) for k, v in d.items()}
    if isinstance(d, list):
        return [redact_dict(x) for x in d]
    if isinstance(d, str):
        return redact_string(d)
    return d


def redact_pii(trace: dict) -> dict:
    """Redact PII from a trace dict for safe YAML output.

    Redacts:
      - customer.row.email    -> <sender_email>
      - customer.row.full_name -> <full_name> ("<first_name> <last_name>")
      - customer.row.phone    -> <phone>
      - order.row.shipping_address.* fields containing digits -> redacted
      - payments[].customer_email -> <sender_email>
      - any email-shaped token in any string field

    Preserves:
      - email.subject, email.body (operator needs actual content)
      - email_id (not PII)
      - numeric fields that aren't PII (e.g. amounts, dates)
    """
    out = json.loads(json.dumps(trace, default=str))  # deep copy via JSON

    # customer
    customer = (out.get("retriever") or {}).get("customer")
    if isinstance(customer, dict):
        row = customer.get("row")
        if isinstance(row, dict):
            if "email" in row and isinstance(row["email"], str):
                row["email"] = "<sender_email>"
            if "full_name" in row and isinstance(row["full_name"], str):
                row["full_name"] = _redact_name(row["full_name"])
            if "phone" in row and isinstance(row["phone"], str):
                row["phone"] = _redact_phone_str(row["phone"])

    # order
    order = (out.get("retriever") or {}).get("order")
    if isinstance(order, dict):
        row = order.get("row")
        if isinstance(row, dict):
            addr = row.get("shipping_address")
            if isinstance(addr, dict):
                for k, v in list(addr.items()):
                    if isinstance(v, str):
                        addr[k] = _redact_address_field(k, v)

    # payments
    payments = (out.get("retriever") or {}).get("payments")
    if isinstance(payments, list):
        for p in payments:
            if isinstance(p, dict) and isinstance(p.get("customer_email"), str):
                p["customer_email"] = "<sender_email>"

    # products
    products = (out.get("retriever") or {}).get("products")
    if isinstance(products, list):
        for prod in products:
            if isinstance(prod, dict):
                for k, v in list(prod.items()):
                    if isinstance(v, str) and k in ("warranty_text",
                                                      "tech_description",
                                                      "tech_details",
                                                      "description"):
                        prod[k] = redact_string(v)

    # input: keep subject + body intact for the operator's reading
    # but redact sender_email
    inp = out.get("input")
    if isinstance(inp, dict) and "sender_email" in inp:
        if isinstance(inp["sender_email"], str):
            inp["sender_email"] = "<sender_email>"

    # input.notes is operator-supplied metadata — keep intact
    return out


def _redact_name(name: str) -> str:
    """Replace a real name with <first_name> <last_name> placeholders."""
    parts = name.strip().split()
    if len(parts) >= 2:
        return "<first_name> <last_name>"
    if len(parts) == 1:
        return "<name>"
    return "<name>"


def _redact_phone_str(s: str) -> str:
    """Replace phone-like runs with <phone>."""
    return _PHONE_RE.sub("<phone>", s)


def _redact_address_field(key: str, value: str) -> str:
    """Redact specific address fields (preserve city/state names)."""
    if key in ("line1", "line2", "zip", "pincode", "zip_code",
              "postal_code"):
        return _PINCODE_RE.sub("<pincode>", redact_string(value))
    return redact_string(value)


# ---------------------------------------------------------------------------
# Phase 1: workflow run
# ---------------------------------------------------------------------------
def _run_one_workflow(eval_set_path: Path, trace_dir: Path,
                       run_id: str, eid: str, row: dict) -> tuple[str, dict, int]:
    """Run a single email's workflow. Returns (eid, trace_dict, ms)."""
    t0 = time.perf_counter()
    email = Email(
        subject=row.get("topic", ""),
        body=row.get("email_content", ""),
        sender_email=row.get("sender_email", ""),
        notes=f"run_id={run_id}, serial={row.get('serial', '?')}",
    )
    wf = Workflow(trace_dir=trace_dir)
    trace = wf.run(eid, email)
    ms = int((time.perf_counter() - t0) * 1000)
    return eid, trace.to_dict(), ms


def run_workflow_phase(
    eval_set_path: Path | str,
    out_dir: Path | str,
    *,
    parallel: int = 5,
    notes: str = "",
) -> str:
    """Phase 1: run the workflow for every email in the eval set,
    in parallel with `parallel` workers. Writes:

      - data/runs/<run_id>/<email_id>.json  (per-email trace)
      - data/runs/<run_id>/workflow.yaml    (stage-wise log, PII-redacted)
      - data/runs/<run_id>/manifest.json    (per-run summary)
    Returns the run_id.
    """
    eval_set_path = Path(eval_set_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    run_id = f"workflow-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    run_dir = out_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Load + normalize the eval set, preserve the original input order
    raw_rows = load_eval_set(eval_set_path)
    rows = [normalize_eval_row(r) for r in raw_rows]
    # Tag with serial (1-indexed)
    for i, r in enumerate(rows, start=1):
        r["serial"] = i

    log.info("phase1: running %d emails with %d workers", len(rows), parallel)
    started_at = datetime.now().isoformat()

    results: dict[str, dict] = {}
    timings_ms: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=parallel) as ex:
        futures = {
            ex.submit(_run_one_workflow, eval_set_path, run_dir, run_id, r["email_id"], r): r
            for r in rows
        }
        for fut in as_completed(futures):
            eid, trace_dict, ms = fut.result()
            results[eid] = trace_dict
            timings_ms[eid] = ms
            log.info("  phase1: %s done in %dms", eid, ms)

    finished_at = datetime.now().isoformat()

    # Write per-run files
    write_workflow_yaml(
        run_dir, run_id, rows, results, timings_ms,
        started_at, finished_at, parallel, notes,
    )
    _write_manifest(
        run_dir, run_id, rows, results, timings_ms,
        started_at, finished_at, parallel, notes,
    )

    log.info("phase1 done: %s", run_dir)
    return run_id


def write_workflow_yaml(
    run_dir: Path,
    run_id: str,
    rows: list[dict],
    results: dict[str, dict],
    timings_ms: dict[str, int],
    started_at: str,
    finished_at: str,
    parallel: int,
    notes: str,
) -> Path:
    """Write the per-run workflow YAML with PII redacted.

    The trace files on disk (data/runs/<run_id>/<email_id>.json) stay
    complete and unredacted. Only the workflow YAML output is
    PII-redacted for safe sharing / archival.
    """
    rec = os.environ.get("RESOLVER_MODEL", "gpt-4.1")
    rf = os.environ.get("REFLEXIVE_MODEL", "gpt-4.1")
    emb = os.environ.get("OPENAI_MODEL_EMBEDDING", "text-embedding-3-small")
    max_tok = os.environ.get("RAG_CHILD_TOKENS", "300")

    emails_out: list[dict] = []
    for r in rows:
        eid = r["email_id"]
        trace = results.get(eid, {})
        redacted = redact_pii(trace)
        emails_out.append({
            "serial": r.get("serial"),
            "email_id": eid,
            "sender_email": "<sender_email>",
            "subject": r.get("topic", ""),
            "body": r.get("email_content", ""),
            "expected": {
                "decision": r.get("decision"),
                "intent": r.get("spec_intent"),
                "linked_order_id": r.get("order_id"),
                "required_clauses": [c.get("clause")
                                       for c in r.get("required_clauses", [])],
            },
            "retriever": redacted.get("retriever", {}),
            "resolver": redacted.get("resolver", {}),
            "reflex": redacted.get("reflexive", {}),
            "final_state": (trace.get("resolver") or {}).get("action", ""),
            "outcome": trace.get("outcome", ""),
            "timing_ms": trace.get("timing_ms", {}),
        })

    out = {
        "schema_version": "1.0",
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "parallelism": parallel,
        "retriever_model": emb,
        "retriever_max_tokens": max_tok,
        "resolver_model": rec,
        "reflexive_model": rf,
        "notes": notes,
        "emails": emails_out,
    }
    p = run_dir / "workflow.yaml"
    p.write_text(yaml.safe_dump(out, sort_keys=False, default_flow_style=False))
    return p


def _write_manifest(
    run_dir: Path, run_id: str, rows, results, timings_ms,
    started_at, finished_at, parallel, notes,
) -> Path:
    """Write a per-run manifest.json (summary only; not the YAML)."""
    from collections import Counter
    decisions = Counter()
    for eid in [r["email_id"] for r in rows]:
        d = ((results.get(eid, {}).get("resolver") or {}).get("action")
              or "error")
        decisions[d] += 1
    payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "n_emails": len(rows),
        "n_succeeded": sum(decisions.values()),
        "decisions": dict(decisions),
        "parallelism": parallel,
        "notes": notes,
    }
    p = run_dir / "manifest.json"
    p.write_text(json.dumps(payload, indent=2, default=str))
    return p


# ---------------------------------------------------------------------------
# Phase 2: judge
# ---------------------------------------------------------------------------
def _judge_one_email(
    judge: JudgeClient, trace: dict, eval_row: dict, metrics: list[dict],
) -> dict:
    """Judge one email across all its rubric metrics.

    Returns a dict suitable for inclusion in the judge YAML.
    """
    metrics_out: list[dict] = []
    failed_metrics: list[dict] = []
    total_in, total_out = 0, 0
    total_cost = 0.0
    for m in metrics:
        mid = m.get("metric_id")
        if not mid:
            continue
        res = _judge_metric(judge, mid, trace, eval_row)
        # Compute cost via metric_tracker
        cost = _estimate_cost(mid, res.tokens_in, res.tokens_out)
        total_in += res.tokens_in
        total_out += res.tokens_out
        total_cost += cost
        entry = {
            "metric_id": mid,
            "score": res.score if hasattr(res, "score") else None,
            "passed": (res.score >= 4) if hasattr(res, "score") and res.score
                      is not None else None,
            "rationale": getattr(res, "rationale", "")[:300],
            "tokens_in": res.tokens_in,
            "tokens_out": res.tokens_out,
            "cost_usd": round(cost, 6),
            "latency_ms": getattr(res, "duration_ms", 0),
        }
        metrics_out.append(entry)
        if entry["passed"] is False:
            failed_metrics.append({
                "metric_id": mid,
                "score": entry["score"],
                "rationale": entry["rationale"],
            })
    return {
        "metrics": metrics_out,
        "failed_metrics": failed_metrics,
        "total_tokens_in": total_in,
        "total_tokens_out": total_out,
        "total_cost_usd": round(total_cost, 6),
    }


def _judge_metric(judge, mid, trace, eval_row):
    """Thin wrapper around the existing score._dispatch_judge."""
    from .score import _dispatch_judge
    return _dispatch_judge(judge, mid, trace, eval_row)


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    from .metric_tracker import _env_rates, _DEFAULT_PRICING
    rates = _env_rates()
    if rates is None:
        rates = _DEFAULT_PRICING.get(model)
    if rates is None:
        return 0.0
    return (tokens_in / 1000.0) * rates["in"] + (tokens_out / 1000.0) * rates["out"]


def run_judge_phase(
    trace_dir: Path | str,
    out_dir: Path | str,
    *,
    parallel: int = 5,
    notes: str = "",
    phase1_run_id: Optional[str] = None,
) -> str:
    """Phase 2: judge every trace in the latest (or named) workflow run.

    Reads the workflow.yaml from trace_dir (or the named subdir if
    phase1_run_id is given) to know which emails were processed and
    to recover the original order. For each email, calls the judge on
    every rubric metric, in parallel with `parallel` workers.

    Writes:
      - data/runs/judge_runs/<judge_run_id>/judge.yaml  (per-run output)
      - data/runs/judge_runs/<judge_run_id>/manifest.json
    Returns the judge_run_id.
    """
    trace_dir = Path(trace_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Discover the workflow run dir
    if phase1_run_id:
        run_dir = trace_dir / phase1_run_id
    else:
        # pick the most recent subdir
        subdirs = sorted([p for p in trace_dir.iterdir() if p.is_dir()])
        if not subdirs:
            raise FileNotFoundError(f"no workflow runs in {trace_dir}")
        run_dir = subdirs[-1]
    workflow_yaml = run_dir / "workflow.yaml"
    if not workflow_yaml.exists():
        raise FileNotFoundError(f"missing {workflow_yaml}")

    wf = yaml.safe_load(workflow_yaml.read_text())
    rows = wf.get("emails", [])
    log.info("phase2: judging %d emails from %s with %d workers",
             len(rows), run_dir, parallel)

    # Reconstruct eval rows (with the original order preserved by serial).
    eval_rows = _reconstruct_eval_rows(rows)

    judge = JudgeClient()
    if not judge.is_configured():
        log.warning("phase2: judge is not configured; "
                    "set JUDGE_BASE_URL / JUDGE_MODEL / JUDGE_API_KEY. "
                    "Falling back to per-metric 'unscored'.")
    tracker = MetricTracker(out_path=out_dir / "metric_costs.json")

    started_at = datetime.now().isoformat()
    out = []
    failed_summary: list[dict] = []
    judge_run_id = f"judge-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    with ThreadPoolExecutor(max_workers=parallel) as ex:
        futures = {}
        for r in rows:
            eid = r["email_id"]
            # Reconstruct the trace from the run_dir trace file (unredacted)
            trace_path = run_dir / f"{eid}.json"
            if not trace_path.exists():
                # fall back to the redacted workflow.yaml copy
                # (we still have the resolver output + retriever context
                # from the redacted form; the judge can use it for
                # faithfulness, intent, side-effect, tone scoring).
                log.warning("phase2: missing trace for %s; using "
                            "redacted workflow.yaml copy", eid)
                trace = {
                    "schema_version": "1.0",
                    "email_id": eid,
                    "input": {"sender_email": r.get("sender_email"),
                              "subject": r.get("subject"),
                              "body": r.get("body")},
                    "retriever": r.get("retriever", {}),
                    "resolver": r.get("resolver", {}),
                    "reflexive": r.get("reflex", {}),
                }
            else:
                trace = json.loads(trace_path.read_text())
            eval_row = eval_rows.get(eid, {})
            metrics = eval_row.get("rubric", [])
            futures[ex.submit(_judge_one_email, judge, trace, eval_row,
                               metrics)] = r
        for fut in as_completed(futures):
            r = futures[fut]
            res = fut.result()
            out.append({
                "serial": r.get("serial"),
                "email_id": r["email_id"],
                "expected": {
                    "decision": r.get("expected", {}).get("decision"),
                    "expected_final_state": r.get("expected", {}).get("decision"),
                },
                "metrics": res["metrics"],
                "failed_metrics": res["failed_metrics"],
                "total_tokens_in": res["total_tokens_in"],
                "total_tokens_out": res["total_tokens_out"],
                "total_cost_usd": res["total_cost_usd"],
            })
            failed_summary.extend([
                {"serial": r.get("serial"),
                 "email_id": r["email_id"],
                 "metric_id": fm["metric_id"],
                 "score": fm["score"],
                 "rationale": fm["rationale"]}
                for fm in res["failed_metrics"]
            ])

    finished_at = datetime.now().isoformat()
    total_in = sum(o["total_tokens_in"] for o in out)
    total_out = sum(o["total_tokens_out"] for o in out)
    total_cost = sum(o["total_cost_usd"] for o in out)
    pass_count = sum(1 for o in out
                     for m in o["metrics"] if m.get("passed") is True)
    fail_count = sum(1 for o in out
                     for m in o["metrics"] if m.get("passed") is False)
    unscored = sum(1 for o in out
                   for m in o["metrics"] if m.get("passed") is None)

    judge_run_dir = out_dir / judge_run_id
    judge_run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "run_id": judge_run_id,
        "phase1_run_id": wf.get("run_id", ""),
        "started_at": started_at,
        "finished_at": finished_at,
        "parallelism": parallel,
        "judge_model": os.environ.get("JUDGE_MODEL", ""),
        "judge_endpoint": os.environ.get("JUDGE_BASE_URL", ""),
        "judge_version": "1.0",
        "notes": notes,
        "emails": out,
        "summary": {
            "total_emails": len(out),
            "total_metrics_evaluated": pass_count + fail_count + unscored,
            "total_pass": pass_count,
            "total_fail": fail_count,
            "total_unscored": unscored,
            "total_tokens_in": total_in,
            "total_tokens_out": total_out,
            "total_cost_usd": round(total_cost, 6),
        },
        "failed": failed_summary,
    }
    (judge_run_dir / "judge.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False, default_flow_style=False)
    )
    (judge_run_dir / "manifest.json").write_text(
        json.dumps({
            "schema_version": "1.0",
            "run_id": judge_run_id,
            "phase1_run_id": wf.get("run_id", ""),
            "n_emails": len(out),
            "total_pass": pass_count,
            "total_fail": fail_count,
            "total_unscored": unscored,
            "total_cost_usd": round(total_cost, 6),
        }, indent=2, default=str)
    )
    tracker.finalize()
    log.info("phase2 done: %s", judge_run_dir)
    return judge_run_id


def _reconstruct_eval_rows(rows: list[dict]) -> dict[str, dict]:
    """Reconstruct {email_id: eval_row-like} from the workflow.yaml
    record. The YAML keeps the golden's expected decision + required
    clauses; we attach a minimal `rubric` from the bundle file.
    """
    from .eval_set import load_eval_set
    out: dict[str, dict] = {}
    bundle_rows = {}
    try:
        bundle_rows = {r["email_id"]: r for r in load_eval_set(
            "data/completeBytemartEvalset/BytemartEvals.yaml"
        )}
    except Exception:
        pass
    for r in rows:
        eid = r["email_id"]
        bundle = bundle_rows.get(eid, {})
        out[eid] = {
            "decision": r.get("expected", {}).get("decision"),
            "rubric": bundle.get("rubric", []),
            "required_clauses": bundle.get("required_clauses", []),
            "spec_intent": bundle.get("spec_intent"),
            "hilt_reason": bundle.get("hilt_reason"),
        }
    return out
