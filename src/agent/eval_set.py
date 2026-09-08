"""src/agent/eval_set.py — AM-004 eval runner (Phase 4D).

Loads data/completeBytemartEvalset/BytemartEvals.yaml through a defensive
normalizer that coerces known YAML emitter bugs (last_updated as
datetime.date, transaction_id as int, amount as float) into strings.
Loops every eval row through Workflow.run(); writes per-email trace
JSONs (schema 1.1) and a run manifest.

Eval queries NEVER touch the database — they live in the YAML / TSV
files only (per Phase 4 design).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from .types import Email
from .workflow import Workflow
from .logger import JSONLinesLogger, get_logger


log = get_logger("src.agent.eval_set")


DEFERRED: bool = False        # Phase 4D: rubric has landed


# ---------------------------------------------------------------------------
# Defensive normalizer
# ---------------------------------------------------------------------------
def _to_str_safe(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, float):
        # Preserve integer-valued floats; otherwise use repr.
        if v.is_integer():
            return str(int(v))
        return repr(v)
    return str(v)


def normalize_eval_row(row: dict) -> dict:
    """Return a deep-copied row with known YAML emitter bugs coerced to str."""
    import copy
    out = copy.deepcopy(row)

    # 1) last_updated -> str
    if "last_updated" in out and not isinstance(out["last_updated"], str):
        out["last_updated"] = _to_str_safe(out["last_updated"])

    # 2) action_sequence[].args{} numeric-looking fields -> str
    seq = out.get("action_sequence") or []
    for step in seq:
        args = step.get("args") or {}
        for k in list(args.keys()):
            if k in ("transaction_id", "order_id") and not isinstance(args[k], str):
                args[k] = _to_str_safe(args[k])
            elif k == "amount":
                args[k] = _to_str_safe(args[k])

    # 3) hilt_reason.amount if present (decimal-string to satisfy downstream)
    hr = out.get("hilt_reason")
    if isinstance(hr, dict) and "amount" in hr:
        hr["amount"] = _to_str_safe(hr["amount"])

    return out


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------
def load_eval_set(path: Path | str) -> list[dict]:
    """Load + normalize the eval YAML. Returns list[normalized_row]."""
    import yaml
    p = Path(path)
    doc = yaml.safe_load(p.read_text())
    rows = doc.get("eval_rows") or []
    return [normalize_eval_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------
def _decision_from(action: str) -> str:
    return {
        "auto_send": "sent",
        "hilt_refund": "pending",
        "hilt_other": "pending",
        "escalate": "human_queue",
    }.get(action or "", "error")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run_eval_set(
    eval_set_path: Path | str,
    trace_dir: Path | str,
    log_path: Optional[Path | str] = None,
    workflow_factory: Optional[Any] = None,
) -> Path:
    """Loop every email through Workflow.run(); emit one trace per email
    plus a manifest at `<trace_dir>/manifest.json`.

    Returns the path to the manifest.
    """
    if DEFERRED:
        raise RuntimeError(
            "run_eval_set is DEFERRED. Set DEFERRED=False in src/agent/eval_set.py."
        )

    trace_dir = Path(trace_dir)
    trace_dir.mkdir(parents=True, exist_ok=True)

    log_path = Path(log_path) if log_path else (trace_dir / "run.jsonl")
    # Fresh run: wipe the log.
    if log_path.exists():
        log_path.unlink()
    jsonl = JSONLinesLogger(log_path)
    jsonl.event("run_start", eval_set=str(eval_set_path),
                trace_dir=str(trace_dir))

    rows = load_eval_set(eval_set_path)

    # Per-run accumulators
    decision_counts = {"auto_send": 0, "hilt_refund": 0,
                       "hilt_other": 0, "escalate": 0}
    timing_total = 0

    factory = workflow_factory or (lambda: Workflow(trace_dir=trace_dir))

    for row in rows:
        email_id = row["email_id"]
        jsonl.event("email_start", email_id=email_id,
                    topic=row.get("topic", ""),
                    spec_intent=row.get("spec_intent", ""))
        email = Email(
            subject=f"[{email_id}] {row.get('topic', '')}",
            body=row.get("email_content", ""),
            sender_email=row.get("sender_email", ""),
        )
        try:
            wf = factory()
            trace = wf.run(email_id, email)
        except Exception as exc:
            log.exception("workflow failed for %s", email_id)
            jsonl.event("email_error", email_id=email_id,
                        level="ERROR", error=f"{type(exc).__name__}: {exc}")
            continue

        decision = (trace.resolver or {}).get("action", "")
        decision_counts[decision] = decision_counts.get(decision, 0) + 1
        timing_total += (trace.timing_ms or {}).get("total", 0)

        jsonl.event("email_done", email_id=email_id,
                    decision=decision,
                    outcome=trace.outcome,
                    timing_ms=trace.timing_ms.get("total", 0))

    manifest = {
        "schema_version": "1.0",
        "eval_set": str(eval_set_path),
        "trace_dir": str(trace_dir),
        # n_emails reflects the rows we attempted; n_succeeded reflects
        # how many produced a final trace (i.e. not errored). They may
        # differ if some traces errored out — the score layer treats
        # them separately.
        "n_emails": len(rows),
        "n_succeeded": sum(decision_counts.values()),
        "decisions": decision_counts,
        "timing_ms_total": timing_total,
        "log_path": str(log_path),
        "finished_at": datetime.now().isoformat(),
    }
    manifest_path = trace_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, default=str, indent=2))

    jsonl.event("run_done", n_emails=len(rows),
                decisions=decision_counts, timing_ms_total=timing_total)
    jsonl.close()

    return manifest_path
