"""src/agent/score.py — AM-005 scorer (Phase 4D).

Three layers over each trace:
  1. Deterministic (always runs):
     - right_tools_called
     - linked_order_resolved
     - correct_action
     - intent_correct
     - no_unnecessary_calls
  2. Field/regex (always runs):
     - no_pii_echo
     - no_fabricated_amounts
     - cites_policy_clause
  3. Judge (LLM-as-judge, runs only if JudgeClient.is_configured):
     - tone_professional, clarity_structure, completeness, matches_register,
       plus row-specific Likert metrics from the eval YAML's rubric[].

The 49-metric vocabulary is a registry keyed by `metric_id`. Metrics
without a registered handler are recorded as `unscored` (not failed).

Outputs:
  - scores.csv — one row per (email_id, metric_id) with value + passed + reason
  - report.md  — aggregate report (decision confusion, per-metric pass rates)
  - (cost.json is written by MetricTracker separately)
"""
from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .logger import get_logger
from .judge import JudgeClient, UnscoredResult


log = get_logger("src.agent.score")


# ---------------------------------------------------------------------------
# PerMetric
# ---------------------------------------------------------------------------
@dataclass
class PerMetric:
    metric_id:   str
    layer:       str         # "deterministic" | "field" | "judge"
    value:       Any = None
    passed:      Optional[bool] = None
    score:       Optional[float] = None     # 1..5; None for binary
    reason:      str = ""
    tokens_in:   int = 0
    tokens_out:  int = 0
    duration_ms: int = 0
    judge_model: str = ""

    def to_dict(self) -> dict:
        return {
            "metric_id":   self.metric_id,
            "layer":       self.layer,
            "value":       self.value,
            "passed":      self.passed,
            "score":       self.score,
            "reason":      self.reason,
            "tokens_in":   self.tokens_in,
            "tokens_out":  self.tokens_out,
            "duration_ms": self.duration_ms,
            "judge_model": self.judge_model,
        }


@dataclass
class EmailScore:
    email_id:        str
    decision_actual:  str = ""
    decision_expected: str = ""
    metrics:         list[PerMetric] = field(default_factory=list)
    weighted_score:  float = 0.0

    def to_dict(self) -> dict:
        return {
            "email_id":         self.email_id,
            "decision_actual":  self.decision_actual,
            "decision_expected": self.decision_expected,
            "weighted_score":   self.weighted_score,
            "metrics":          [m.to_dict() for m in self.metrics],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _amounts_in_text(s: str) -> list[str]:
    """Extract all ₹-style numbers in a text. 54,990 / ₹54,990 / Rs.54990 etc."""
    if not s:
        return []
    candidates = re.findall(
        r"(?:₹|rs\.?|inr)\s*([0-9][0-9,]*\.?[0-9]*)",
        s, flags=re.IGNORECASE,
    )
    plain = re.findall(r"\b([0-9][0-9,]{2,})\b", s)
    return [c for c in candidates + plain if c]


def _norm_amount(a: str) -> str:
    """Normalize an amount string to a canonical form (digits + at most one dot).

    Strips thousands separators, surrounding whitespace, leading/trailing
    zeros from the integer part only (NOT the fractional part), and
    collapses a single trailing dot. Examples:
      "54,990"   -> "54990"
      "54990"    -> "54990"
      "55090.00" -> "55090.00"
      "100"      -> "100"
      "0"        -> "0"
    """
    s = (a or "").strip().replace(",", "")
    if not s:
        return "0"
    if "." in s:
        int_part, _, frac = s.partition(".")
        int_part = int_part.lstrip("0") or "0"
        frac = frac.rstrip("0")
        s = f"{int_part}.{frac}" if frac else int_part
    else:
        s = s.lstrip("0") or "0"
    return s


def _clause_re_in_draft(draft: str) -> list[tuple[str, str]]:
    """Find (policy_id, clause) pairs in the draft text.

    Accepts several citation forms:
      - "returns §3.1"        (policy-prefix, §symbol, clause)
      - "return §3.1"          (short policy alias)
      - "policy 3.1"          (no §symbol — fall back to numeric)
      - "section 3.1"          (prose section reference)
      - "§3.1"                (clause-only, no policy)
      - "clause 3.1"
      - "shipping 3.5"        (word + number, no §)

    Returns a list of (policy_id_lower, clause_str) tuples. The
    policy_id may be "" when the citation has no policy prefix.
    """
    if not draft:
        return []
    out: list[tuple[str, str]] = []
    # Pattern 1: "<policy> §<n>" (the canonical form the resolver
    # is supposed to use)
    pat_canonical = re.compile(
        r"\b([a-z][a-z_-]*?)(?:\s*[- ]?policy)?\s*§\s*(\d{1,2}(?:\.\d{1,2})*)",
        re.IGNORECASE,
    )
    for m in pat_canonical.finditer(draft):
        out.append((m.group(1).lower(), m.group(2)))
    # Pattern 2: "<policy> section <n>" or "<policy> clause <n>"
    pat_section = re.compile(
        r"\b([a-z][a-z_-]*?)\s+(?:section|clause)\s+(\d{1,2}(?:\.\d{1,2})*)",
        re.IGNORECASE,
    )
    for m in pat_section.finditer(draft):
        # Avoid double-counting if already matched by the canonical form
        if (m.group(1).lower(), m.group(2)) not in out:
            out.append((m.group(1).lower(), m.group(2)))
    # Pattern 3: §<n> with no policy prefix (clause-only citation)
    pat_clause_only = re.compile(r"§\s*(\d{1,2}(?:\.\d{1,2})*)")
    for m in pat_clause_only.finditer(draft):
        already = any(c == m.group(1) for _, c in out)
        if not already:
            out.append(("", m.group(1)))
    return out


# ---------------------------------------------------------------------------
# Deterministic checks
# ---------------------------------------------------------------------------
def check_right_tools_called(trace: dict, golden: dict) -> PerMetric:
    """Every tool in the golden action_sequence must appear in retriever.tool_calls[]."""
    seq = golden.get("action_sequence") or []
    expected = [step.get("tool") for step in seq if step.get("tool")]
    if not expected:
        return PerMetric("right_tools_called", "deterministic", passed=True,
                         reason="no expected tools in action_sequence")
    called = [c.get("tool") for c in (trace.get("retriever") or {}).get("tool_calls", [])]
    missing = [t for t in expected if t not in called]
    return PerMetric(
        metric_id="right_tools_called",
        layer="deterministic",
        value={"expected": expected, "called": called, "missing": missing},
        passed=not missing,
        reason=("OK" if not missing else f"missing tools: {missing}"),
    )


def check_linked_order_resolved(trace: dict, golden: dict) -> PerMetric:
    expected_oid = (golden.get("linked_order_id") or "").strip()
    if not expected_oid:
        return PerMetric("linked_order_resolved", "deterministic", passed=True,
                         reason="no linked_order_id in golden row")
    order = (trace.get("retriever") or {}).get("order") or {}
    actual_oid = ((order.get("row") or {}) if order else {}).get("order_id")
    ok = bool(order.get("found")) and (actual_oid == expected_oid)
    return PerMetric(
        metric_id="linked_order_resolved",
        layer="deterministic",
        value={"expected": expected_oid, "actual": actual_oid},
        passed=ok,
        reason=("OK" if ok else f"expected {expected_oid}, got {actual_oid}"),
    )


def check_correct_action(trace: dict, golden: dict) -> PerMetric:
    actual = (trace.get("resolver") or {}).get("action", "")
    expected = golden.get("decision", "")
    ok = (actual == expected)
    return PerMetric(
        metric_id="correct_action",
        layer="deterministic",
        value={"actual": actual, "expected": expected},
        passed=ok,
        reason=("OK" if ok else f"expected {expected}, got {actual}"),
    )


# ---------------------------------------------------------------------------
# Workflow-level categorical action (hilt_refund ≡ hilt_other ≡ hilt)
# ---------------------------------------------------------------------------
# At the workflow level, all HILT_* actions route to the same operator
# queue (pending). The 4-way bundle distinction (hilt_refund vs
# hilt_other) is preserved in the trace + the strict 4-way
# `correct_action` check above. This categorical check is the
# "did the workflow route the email to the right place?" view.

_HILT_SUB_ACTIONS = frozenset({"hilt_refund", "hilt_other"})


def _action_category(action: str) -> str:
    """Map a 4-way action to the workflow's 3-way outcome category.

    hilt_refund and hilt_other both collapse to "hilt" because they
    share the same operator-queue outcome ("pending"). Unknown
    actions are passed through unchanged so future 5th/6th actions
    still score (recorded as "fail" against the gold category).
    """
    if action in _HILT_SUB_ACTIONS:
        return "hilt"
    return action


def check_correct_action_category(trace: dict, golden: dict) -> PerMetric:
    """Categorical action check: hilt_refund / hilt_other both count as hilt.

    Independent of the strict 4-way `correct_action` which keeps the
    bundle's sub-action distinction for downstream scoring.
    """
    actual_raw = (trace.get("resolver") or {}).get("action", "")
    expected_raw = golden.get("decision", "")
    actual_cat = _action_category(actual_raw)
    expected_cat = _action_category(expected_raw)
    ok = bool(actual_cat) and actual_cat == expected_cat
    return PerMetric(
        metric_id="correct_action_category",
        layer="deterministic",
        value={"actual": actual_raw, "expected": expected_raw,
               "actual_cat": actual_cat, "expected_cat": expected_cat},
        passed=ok,
        reason=("OK" if ok else
                f"expected category {expected_cat!r} (from {expected_raw!r}), "
                f"got {actual_cat!r} (from {actual_raw!r})"),
    )


def check_intent_correct(trace: dict, golden: dict) -> PerMetric:
    actual = (trace.get("resolver") or {}).get("intent", "")
    expected = golden.get("spec_intent", "")
    ok = bool(expected) and (actual == expected)
    return PerMetric(
        metric_id="intent_correct",
        layer="deterministic",
        value={"actual": actual, "expected": expected},
        passed=ok,
        reason=("OK" if ok else f"expected {expected}, got {actual}"),
    )


def check_no_unnecessary_calls(trace: dict, golden: dict) -> PerMetric:
    calls = (trace.get("retriever") or {}).get("tool_calls", [])
    if not calls:
        return PerMetric("no_unnecessary_calls", "deterministic", passed=False,
                         reason="no tool calls recorded")
    first = calls[0].get("tool")
    if first != "lookup_customer":
        return PerMetric(
            metric_id="no_unnecessary_calls", layer="deterministic",
            value={"first_tool": first, "n_calls": len(calls)},
            passed=False,
            reason=f"first call was {first!r}, expected lookup_customer",
        )
    if len(calls) > 6:
        return PerMetric(
            metric_id="no_unnecessary_calls", layer="deterministic",
            value={"n_calls": len(calls)},
            passed=False,
            reason=f"too many calls: {len(calls)} (max 6)",
        )
    return PerMetric("no_unnecessary_calls", "deterministic",
                     value={"n_calls": len(calls)}, passed=True, reason="OK")


DETERMINISTIC_CHECKS: list[Callable[[dict, dict], PerMetric]] = [
    check_right_tools_called,
    check_linked_order_resolved,
    check_correct_action,
    check_correct_action_category,
    check_intent_correct,
    check_no_unnecessary_calls,
]


# ---------------------------------------------------------------------------
# Field/regex checks
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b")
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s-]?)?\d{5}[\s-]?\d{5}\b")


def check_no_pii_echo(trace: dict, golden: dict) -> PerMetric:
    draft = (trace.get("resolver") or {}).get("draft") or ""
    body   = (trace.get("input")   or {}).get("body", "")
    if not draft:
        return PerMetric("no_pii_echo", "field", passed=True,
                         reason="no draft to check")
    # Collect PII present in the inbound (allowed in draft).
    inbound_emails = set(_EMAIL_RE.findall(body))
    inbound_phones = set(_digits_only(p) for p in _PHONE_RE.findall(body))
    inbound_emails.add("")  # so empty match is in set
    inbound_phones.add("")  # same

    emails_in_draft = _EMAIL_RE.findall(draft)
    new_emails = [e for e in emails_in_draft if e not in inbound_emails]
    phones_in_draft = [_digits_only(p) for p in _PHONE_RE.findall(draft)]
    new_phones = [p for p in phones_in_draft
                  if p and p not in inbound_phones and len(p) >= 10]

    if new_emails or new_phones:
        return PerMetric(
            metric_id="no_pii_echo", layer="field",
            value={"new_emails": new_emails, "new_phones": new_phones},
            passed=False,
            reason=f"draft echoes PII not in inbound: "
                   f"emails={new_emails} phones={new_phones}",
        )
    return PerMetric("no_pii_echo", "field", passed=True, reason="OK")


def check_no_fabricated_amounts(trace: dict, golden: dict) -> PerMetric:
    draft = (trace.get("resolver") or {}).get("draft") or ""
    if not draft:
        return PerMetric("no_fabricated_amounts", "field", passed=True,
                         reason="no draft to check")

    # Gather ground-truth amounts from the retriever context.
    order = ((trace.get("retriever") or {}).get("order") or {}).get("row") or {}
    amounts: set[str] = set()
    if order:
        if order.get("total_amount") is not None:
            amounts.add(_norm_amount(str(int(float(order["total_amount"])))))
        for it in (order.get("items") or []):
            for f in ("unit_price", "line_total"):
                v = it.get(f)
                if v is not None:
                    amounts.add(_norm_amount(str(int(float(v)))))
    for p in (trace.get("retriever") or {}).get("payments", []):
        v = p.get("amount")
        if v is not None:
            amounts.add(_norm_amount(str(int(float(v)))))

    draft_amounts = _amounts_in_text(draft)
    new_amounts = []
    for a in draft_amounts:
        norm = _norm_amount(a)
        if norm and norm not in amounts:
            new_amounts.append(a)
    if new_amounts:
        return PerMetric(
            metric_id="no_fabricated_amounts", layer="field",
            value={"draft_amounts": draft_amounts,
                   "ground_truth_amounts": sorted(amounts)},
            passed=False,
            reason=f"draft cites amounts not in retriever context: {new_amounts}",
        )
    return PerMetric("no_fabricated_amounts", "field", passed=True, reason="OK")


def check_cites_policy_clause(trace: dict, golden: dict) -> PerMetric:
    """Draft must cite a policy_id + clause that appears in the retrieved parents."""
    draft = (trace.get("resolver") or {}).get("draft") or ""
    if not draft:
        return PerMetric("cites_policy_clause", "field", passed=True,
                         reason="no draft to check")
    policies = (trace.get("retriever") or {}).get("policies") or []
    if not policies:
        # No policy hits — citation check is vacuously true; Resolver
        # probably didn't need to cite. We pass.
        return PerMetric("cites_policy_clause", "field", passed=True,
                         reason="no policy hits in retriever context")

    # Build a (policy_id_aliases, clause) set from retrieved parents.
    available: set[tuple[str, str]] = set()
    for p in policies:
        clause_refs = p.get("clause_refs") or []
        for c in clause_refs:
            available.add((p.get("doc_id", ""), c))
            # also accept a short alias (e.g. doc_id=refund-policy -> "refund")
            short = (p.get("doc_id") or "").split("-")[0]
            if short:
                available.add((short, c))

    cited = _clause_re_in_draft(draft)
    if not cited:
        return PerMetric(
            metric_id="cites_policy_clause", layer="field",
            value={"cited": [], "available": list(available)},
            passed=False,
            reason="draft does not cite any policy §clause",
        )

    for pid, clause in cited:
        # Match either exact doc_id or short prefix
        candidates = {(pid, clause), (pid.split("-")[0], clause)}
        if candidates & available:
            return PerMetric(
                metric_id="cites_policy_clause", layer="field",
                value={"cited": [(pid, clause)]},
                passed=True,
                reason=f"cited {pid} §{clause} matches retrieved parents",
            )

    return PerMetric(
        metric_id="cites_policy_clause", layer="field",
        value={"cited": cited, "available": list(available)},
        passed=False,
        reason=f"cited {cited} not in retrieved parents",
    )


FIELD_CHECKS: list[Callable[[dict, dict], PerMetric]] = [
    check_no_pii_echo,
    check_no_fabricated_amounts,
    check_cites_policy_clause,
]


# ---------------------------------------------------------------------------
# Scorer entry point
# ---------------------------------------------------------------------------
def score_traces(
    trace_golden_pairs: Iterable[tuple[dict, dict]],
    *,
    judge: Optional[JudgeClient] = None,
    eval_set_path: Optional[Path | str] = None,
    run_judge: bool = True,
) -> list[EmailScore]:
    """Score each (trace, golden) pair across all 3 layers.

    `judge` is optional. If not configured, judge metrics are marked
    'unscored' (not failed).

    If `eval_set_path` is provided, the function loads the YAML and
    attaches the per-row `rubric[]` list to each golden so judge-layer
    metrics are dispatched.
    """
    eval_rows: dict[str, dict] = {}
    if eval_set_path:
        from .eval_set import load_eval_set
        eval_rows = {r["email_id"]: r for r in load_eval_set(eval_set_path)}

    results: list[EmailScore] = []
    for trace, golden in trace_golden_pairs:
        email_id = trace.get("email_id", "?")
        es = EmailScore(
            email_id=email_id,
            decision_actual=(trace.get("resolver") or {}).get("action", ""),
            decision_expected=golden.get("decision", ""),
        )
        # 1) deterministic
        for chk in DETERMINISTIC_CHECKS:
            es.metrics.append(chk(trace, golden))
        # 2) field/regex
        for chk in FIELD_CHECKS:
            es.metrics.append(chk(trace, golden))
        # 3) judge
        eval_row = eval_rows.get(email_id, {})
        rubric = eval_row.get("rubric") or []
        # Phase 5F: surface the Reflexive's `malformed` flag in the
        # deterministic metrics so the operator can see when the
        # LLM-based Reflexive fell back to deterministic path because of a malformed
        # response.
        if (trace.get("reflexive") or {}).get("malformed"):
            es.metrics.append(PerMetric(
                metric_id="reflexive_malformed",
                layer="deterministic",
                passed=False,
                value={"reason": "LLM-based Reflexive returned malformed "
                         "JSON; workflow used deterministic fallback"},
                reason=("reflexive JSON was malformed; falling back to "
                        "deterministic fallback scoring"),
            ))
        if run_judge and judge is not None and judge.is_configured():
            for m in rubric:
                mid = m.get("metric_id")
                if not mid:
                    continue
                res = _dispatch_judge(judge, mid, trace, eval_row)
                es.metrics.append(res)
        else:
            for m in rubric:
                mid = m.get("metric_id")
                if not mid:
                    continue
                es.metrics.append(PerMetric(
                    metric_id=mid, layer="judge",
                    passed=None, score=None,
                    reason="unscored: judge not configured",
                ))

        # Weighted score: sum(weight_i * passed_i) / sum(weight_i) over
        # binary metrics; Likert metrics are normalized 0..1.
        weights: list[tuple[float, float]] = []
        for m in es.metrics:
            w = _metric_weight(m.metric_id, eval_row)
            if w <= 0:
                continue
            if m.passed is True:
                weights.append((w, 1.0))
            elif m.passed is False:
                weights.append((w, 0.0))
            elif m.score is not None:
                # Likert 1..5 normalized to 0..1
                s = max(0.0, min(1.0, (float(m.score) - 1.0) / 4.0))
                weights.append((w, s))
        if weights:
            es.weighted_score = sum(s for _, s in weights) / sum(w for w, _ in weights)
        else:
            es.weighted_score = 0.0
        results.append(es)

    return results


# ---------------------------------------------------------------------------
# Judge-layer dispatch
# ---------------------------------------------------------------------------
def _metric_weight(metric_id: str, eval_row: dict) -> float:
    """Return the per-metric weight from the eval row's rubric (default 1.0)."""
    for m in (eval_row.get("rubric") or []):
        if m.get("metric_id") == metric_id:
            try:
                return float(m.get("weight", 1.0))
            except (TypeError, ValueError):
                return 1.0
    return 1.0


_JUDGE_PROMPT_TPL = """\
You are scoring one metric on a customer-support response.

Metric: {metric_id}
Type: {mtype}
Expected (rubric): {expected}

Email (inbound):
Subject: {subject}
From: {sender}
Body: {body}

Agent draft:
{draft}

Retrieved context (truncated):
{context}

Score 1-5 according to the metric definition. Return JSON only:
{{"score": <int 1-5>, "rationale": "<one short sentence>"}}
"""


def _dispatch_judge(judge: JudgeClient, metric_id: str,
                    trace: dict, eval_row: dict) -> PerMetric:
    """Send a judge call for one metric; return PerMetric."""
    m = next((mm for mm in (eval_row.get("rubric") or [])
              if mm.get("metric_id") == metric_id), {})
    mtype = m.get("type", "likert")
    expected = m.get("expected", m.get("expected_min", "n/a"))
    subject = (trace.get("input") or {}).get("subject", "")
    sender  = (trace.get("input") or {}).get("sender_email", "")
    body    = (trace.get("input") or {}).get("body", "")
    draft   = (trace.get("resolver") or {}).get("draft", "")
    ctx     = json.dumps(trace.get("retriever", {}), default=str)[:2000]
    sys_p = (
        "You are an impartial evaluator of a customer-support AI. "
        "Score the metric on the given draft. Always return valid JSON."
    )
    user_p = _JUDGE_PROMPT_TPL.format(
        metric_id=metric_id, mtype=mtype, expected=expected,
        subject=subject, sender=sender, body=body, draft=draft, context=ctx,
    )
    res = judge.judge(system=sys_p, user=user_p, metric_id=metric_id)

    if isinstance(res, UnscoredResult):
        return PerMetric(
            metric_id=metric_id, layer="judge",
            passed=None, score=None,
            reason=f"unscored: {res.reason}",
        )
    return PerMetric(
        metric_id=metric_id, layer="judge",
        value={"score": res.score, "rationale": res.rationale},
        passed=(res.score >= 4),
        score=res.score,
        tokens_in=res.tokens_in, tokens_out=res.tokens_out,
        duration_ms=res.duration_ms, judge_model=res.model,
        reason=res.rationale,
    )


# ---------------------------------------------------------------------------
# CSV / report writers
# ---------------------------------------------------------------------------
def write_scores_csv(scores: list[EmailScore], out_path: Path | str) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not scores:
        out_path.write_text("email_id,decision_actual,decision_expected,weighted_score\n")
        return out_path
    metric_ids = []
    seen = set()
    for s in scores:
        for m in s.metrics:
            if m.metric_id not in seen:
                seen.add(m.metric_id)
                metric_ids.append(m.metric_id)
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["email_id", "decision_actual", "decision_expected",
                    "weighted_score"] + metric_ids)
        for s in scores:
            row = [s.email_id, s.decision_actual, s.decision_expected,
                   f"{s.weighted_score:.4f}"]
            value_map = {m.metric_id: m for m in s.metrics}
            for mid in metric_ids:
                m = value_map.get(mid)
                if m is None:
                    row.append("")
                elif m.passed is True:
                    row.append("pass")
                elif m.passed is False:
                    row.append("fail")
                elif m.score is not None:
                    row.append(f"score:{m.score}")
                else:
                    row.append("unscored")
            w.writerow(row)
    return out_path


def write_report(scores: list[EmailScore], out_path: Path | str,
                 manifest: Optional[dict] = None) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# ByteMart Email-Evals — Run Report", ""]
    if manifest:
        lines += [
            f"- Eval set: `{manifest.get('eval_set', '?')}`",
            f"- N emails: {manifest.get('n_emails', len(scores))}",
            f"- Total time: {manifest.get('timing_ms_total', 0)} ms",
            "",
        ]
    # Decision distribution: show both the bundle's 4-way
    # (hilt_refund vs hilt_other preserved) and the workflow's
    # 3-way outcome category (hilt_refund + hilt_other collapsed).
    actual_counts: dict[str, int] = {}
    expected_counts: dict[str, int] = {}
    actual_cat: dict[str, int] = {}
    expected_cat: dict[str, int] = {}
    for s in scores:
        actual_counts[s.decision_actual]   = actual_counts.get(s.decision_actual, 0) + 1
        expected_counts[s.decision_expected] = expected_counts.get(s.decision_expected, 0) + 1
        a = _action_category(s.decision_actual)
        e = _action_category(s.decision_expected)
        actual_cat[a]   = actual_cat.get(a, 0) + 1
        expected_cat[e] = expected_cat.get(e, 0) + 1

    lines += ["## Decision distribution — 4-way (bundle sub-action)", ""]
    lines.append("| decision | actual | expected |")
    lines.append("|---|---|---|")
    for k in ("auto_send", "hilt_refund", "hilt_other", "escalate"):
        lines.append(f"| {k} | {actual_counts.get(k, 0)} | {expected_counts.get(k, 0)} |")
    lines.append("")

    lines += ["## Decision distribution — 3-way (workflow outcome category)", ""]
    lines.append("| category | actual | expected |")
    lines.append("|---|---|---|")
    for k in ("auto_send", "hilt", "escalate"):
        lines.append(f"| {k} | {actual_cat.get(k, 0)} | {expected_cat.get(k, 0)} |")
    lines.append("")

    # Per-metric pass rates
    lines += ["## Per-metric pass rates", ""]
    lines.append("| metric | layer | pass | fail | unscored |")
    lines.append("|---|---|---|---|---|")
    metric_agg: dict[str, dict] = {}
    for s in scores:
        for m in s.metrics:
            agg = metric_agg.setdefault(m.metric_id, {
                "layer": m.layer, "pass": 0, "fail": 0, "unscored": 0,
            })
            if m.passed is True:
                agg["pass"] += 1
            elif m.passed is False:
                agg["fail"] += 1
            else:
                agg["unscored"] += 1
    for mid, agg in sorted(metric_agg.items()):
        lines.append(f"| {mid} | {agg['layer']} | {agg['pass']} | "
                     f"{agg['fail']} | {agg['unscored']} |")
    lines.append("")

    # Average weighted score
    if scores:
        avg = sum(s.weighted_score for s in scores) / len(scores)
        lines += ["", f"## Avg weighted score: {avg:.4f}"]
    lines.append("")
    out_path.write_text("\n".join(lines))
    return out_path
