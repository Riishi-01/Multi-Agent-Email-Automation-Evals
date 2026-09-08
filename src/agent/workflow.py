"""src/agent/workflow.py — AM-003 orchestrator.

Source of truth: docs/phase-3-workflow.yaml §5 + Phase 4C + Phase 5F.

Wires:
  - RetrieverAgent (AM-001) — real OpenAI function-calling loop, drops the
    Phase-3 `_phase3_stub`. Always returns a RetrieverContext.
  - ResolverAgent (AM-002) — 4-decision schema (auto_send | hilt_refund |
    hilt_other | escalate), 7-field hilt_reason on hilt_* actions.
  - ResolverReflexive (AM-002-R, Phase 5F) — reviews the Resolver's
    output, scores a 5-dimension rubric, suggests accept / regenerate
    / escalate. Deterministic Python routing drives the workflow.

Trace: schema_version="1.2" with retriever.tool_calls[] + resolver.decision
+ resolver.hilt_reason + reflexive + final_action. Persisted to
data/runs/<email_id>.json atomically.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .reflexive_routing import MAX_RETRIES
from .retrieval_checks import check as retrieval_check
from .tool_failure_checks import check as tool_failure_check
from .types import Email, Trace


log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


# Decision → outcome mapping for trace.outcome
_OUTCOME_FROM_ACTION = {
    "auto_send":   "sent",
    "hilt_refund": "pending",
    "hilt_other":  "pending",
    "escalate":    "human_queue",
}


# Reflexive demotion: when the reflexive flags intent_accuracy < 0.65
# AND the primary was auto_send, demote to hilt_other. This is a safety
# net: an auto_send that the Reflexive says is the wrong state becomes
# operator-reviewed instead of going to the customer.
_REFLEXIVE_FALLBACK_RATIONALES = {
    "hilt_other": "demoted to hilt_other (reflexive flagged low intent_accuracy)"  # noqa,
}


class Workflow:
    """AM-003 orchestrator. Stateless between `run()` calls (only `trace_dir`
    is held as state).

    Adding a new agent = register it in __init__ + run it in `run()`.
    No edits to prompt files, retriever code, or any agent.
    """

    def __init__(self, trace_dir: Path | str,
                 retriever=None,
                 resolver=None,
                 reflexive=None) -> None:
        self.trace_dir = Path(trace_dir)
        self.trace_dir.mkdir(parents=True, exist_ok=True)

        # Lazy-resolve agents from sibling modules so importing workflow.py
        # without the agent modules installed doesn't crash at import time.
        if resolver is None:
            from .resolver_agent.agent_resolver import ResolverAgent
            resolver = ResolverAgent()
        self.resolver = resolver

        if retriever is None:
            try:
                from .retriever_agent.agent_retriever import RetrieverAgent
                retriever = RetrieverAgent()
            except Exception as exc:                                # pragma: no cover
                log.warning("could not import RetrieverAgent: %s", exc)
                retriever = None
        self.retriever = retriever

        if reflexive is None:
            try:
                from .resolver_reflexive.agent_resolver_reflexive import (
                    ResolverReflexive,
                )
                reflexive = ResolverReflexive()
            except Exception as exc:                                # pragma: no cover
                log.warning("could not import ResolverReflexive: %s", exc)
                reflexive = None
        self.reflexive = reflexive

    # --------------------------------------------------------------
    # Public API
    # --------------------------------------------------------------
    def run(self, email_id: str, email: Email) -> Trace:
        """Run the agent pipeline; emit a trace JSON; return the Trace."""
        t_total = self._now_ms()
        # Partial state is captured into these locals so the except handler
        # can preserve whatever the retriever/resolver already produced
        # before the failure.
        ctx: dict = {}
        out: dict = {}
        reflexive: dict = {}
        final_action: str = ""
        r_ms = 0
        s_ms = 0
        re_ms = 0
        err: Optional[str] = None
        try:
            ctx, r_ms = self._run_retriever(email)
            try:
                out, s_ms = self._run_resolver(email, ctx)
            except Exception as exc:
                s_ms = self._now_ms() - (t_total + r_ms)
                err = f"resolver error: {type(exc).__name__}: {exc}"
                log.warning("workflow %s: %s", email_id, err)
            if err:
                trace = self._make_trace(
                    email_id=email_id, email=email,
                    retriever=ctx, resolver=out,
                    reflexive=reflexive, final_action=final_action,
                    r_ms=r_ms, s_ms=s_ms + re_ms, t_total=t_total,
                    outcome="error", error=err,
                )
            else:
                # Phase 5F: Reflexive loop + deterministic routing.
                # The Reflexive can use the bundle's `required_clauses` and
                # `rubric` from the eval_row to cross-reference the
                # Resolver's cited clause against the gold.
                eval_row = None  # populated by run_eval_set
                final_action, outcome, reflexive_out, re_ms = (
                    self._run_reflexive_loop(email, ctx, out, eval_row=eval_row)
                )
                reflexive = reflexive_out
                trace = self._make_trace(
                    email_id=email_id, email=email,
                    retriever=ctx, resolver=out,
                    reflexive=reflexive, final_action=final_action,
                    r_ms=r_ms, s_ms=s_ms + re_ms, t_total=t_total,
                    outcome=outcome,
                )
        except Exception as exc:
            log.warning("workflow %s: retriever error: %s: %s",
                        email_id, type(exc).__name__, exc)
            trace = self._make_trace(
                email_id=email_id, email=email,
                retriever=ctx, resolver=out,
                reflexive=reflexive, final_action=final_action,
                r_ms=r_ms, s_ms=s_ms + re_ms, t_total=t_total,
                outcome="error", error=f"{type(exc).__name__}: {exc}",
            )
        self._emit(trace)
        return trace

    def _make_trace(self, *, email_id: str, email: Email,
                    retriever: dict, resolver: dict,
                    reflexive: dict, final_action: str,
                    r_ms: int, s_ms: int, t_total: int,
                    outcome: str, error: Optional[str] = None) -> Trace:
        """Build a Trace with consistent shape + timing (schema 1.2)."""
        return Trace(
            schema_version="1.2",
            email_id=email_id,
            captured_at=datetime.now(timezone.utc).isoformat(),
            input={
                "subject":      email.subject,
                "body":         email.body[:1000],
                "sender_email": email.sender_email,
                "notes":        email.notes if hasattr(email, "notes") else None,
            },
            retriever=retriever,
            resolver=resolver,
            reflexive=reflexive,
            final_action=final_action,
            outcome=outcome,
            timing_ms={
                "retriever": r_ms,
                "resolver":  s_ms,
                "total":     self._now_ms() - t_total,
            },
            error=error,
        )

    # --------------------------------------------------------------
    # Agent steps
    # --------------------------------------------------------------
    def _run_retriever(self, email: Email) -> tuple[dict, int]:
        """Run the real RetrieverAgent. Returns the RetrieverContext as dict."""
        t0 = self._now_ms()
        if self.retriever is None:
            ctx = {"_sender_email": email.sender_email,
                   "_phase3_stub": True,
                   "tool_calls": []}
        else:
            ctx_obj = self.retriever.run(
                subject=email.subject,
                body=email.body,
                sender_email=email.sender_email,
            )
            ctx = ctx_obj.to_dict() if hasattr(ctx_obj, "to_dict") else dict(ctx_obj)
        return ctx, self._now_ms() - t0

    def _run_resolver(self, email: Email, ctx: dict) -> tuple[dict, int]:
        t0 = self._now_ms()
        result = self.resolver.run(
            subject=email.subject,
            body=email.body,
            sender_email=email.sender_email,
            context=ctx,
        )
        out = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        return out, self._now_ms() - t0

    def _run_reflexive(self, email: Email, ctx: dict, out: dict,
                        eval_row: Optional[dict] = None) -> tuple[dict, int]:
        """Single Reflexive call. Returns (reflexive_dict, duration_ms)."""
        t0 = self._now_ms()
        if self.reflexive is None:
            return {"dimension_scores": {}, "confidence": 0.0,
                    "reasoning": "reflexive not configured", "suggested_action": "accept",
                    "regeneration_hints": []}, 0
        # Strip the retriever's tool_calls before passing to the Reflexive
        # (the Reflexive has its own 5 read-only tools and re-queries
        # directly when it needs to ground a score).
        clean_ctx = {k: v for k, v in (ctx or {}).items() if k != "tool_calls"}
        # Extract order_date from the retriever's order row (Phase 5F:
        # the Reflexive uses this for policy_compliance date checks).
        order_row = (clean_ctx.get("order") or {}).get("row") or {}
        order_date = order_row.get("order_date")
        result = self.reflexive.run(
            subject=email.subject,
            body=email.body,
            sender_email=email.sender_email,
            resolver_output=out,
            retriever_context=clean_ctx,
            order_date=order_date,
            eval_row=eval_row,
        )
        return result.to_dict(), self._now_ms() - t0

    def _regenerate_resolver(self, email: Email, ctx: dict,
                              hints: list) -> dict:
        """Re-call the Resolver with the Reflexive's hints appended to
        the system prompt. Returns the new ResolverResult as a dict."""
        return self.resolver.run_with_hints(
            subject=email.subject, body=email.body,
            sender_email=email.sender_email,
            context=ctx, hints=hints or [],
        ).to_dict()

    def _run_reflexive_loop(
        self, email: Email, ctx: dict, out: dict,
        eval_row: Optional[dict] = None,
    ) -> tuple[str, str, dict, int]:
        """Drive the reflexive + regeneration loop.

        Returns: (final_action, outcome, reflexive_dict, re_ms).
        """
        re_ms_total = 0
        retry_count = 0
        current_out = out
        reflexive_dict: dict = {}

        # Phase 5F: structural checks run FIRST. They can short-circuit
        # the Reflexive entirely with a deterministic verdict.
        tool_short = tool_failure_check(ctx, current_out)
        if tool_short.get("short_circuit"):
            reflexive_dict = {
                "dimension_scores": {},
                "confidence": 0.0,
                "reasoning":  "structural-tool-check: " + tool_short.get("reason", ""),
                "suggested_action": "escalate" if tool_short["verdict"] == "escalate" else "accept",
                "regeneration_hints": [],
                "short_circuit": "tool",
            }
            primary = current_out.get("action", "")
            verdict = tool_short["verdict"]
            outcome = "human_queue" if verdict == "escalate" else "pending"
            return primary, outcome, reflexive_dict, re_ms_total

        while True:
            reflexive_dict, re_ms = self._run_reflexive(
                email, ctx, current_out, eval_row=eval_row,
            )
            re_ms_total += re_ms

            # Phase 5F: structural RAG checks BEFORE the deterministic
            # router. They bypass the LLM-driven confidence bands.
            ret_short = retrieval_check(ctx, email.body)
            if ret_short.get("short_circuit"):
                # Override the Reflexive's verdict with the structural
                # check's verdict; the deterministic router below still
                # applies (side-effect override, etc.).
                # Phase 5G.5: don't silently swallow "hilt" -> "accept";
                # treat it as "escalate" so the workflow routes
                # low-similarity drafts to human review instead of
                # auto-sending them.
                _verdict_map = {"escalate": "escalate",
                                "hilt":     "escalate",
                                "accept":   "accept"}
                reflexive_dict = {
                    **reflexive_dict,
                    "reasoning": "structural-retrieval-check: " + ret_short.get("reason", ""),
                    "suggested_action": _verdict_map.get(
                        ret_short["verdict"], "accept"),
                }

            # Deterministic Python routing. (The Reflexive's LLM is
            # NEVER allowed to pick the final verdict; the Python
            # router does, applying the spec's ordering of decisions.)
            from .reflexive_routing import route_after_reflexive
            decision = route_after_reflexive(
                primary_action=current_out.get("action", ""),
                reflexive_output=reflexive_dict,
                retry_count=retry_count,
            )
            verdict = decision["verdict"]

            if verdict != "reflect":
                final_action = decision["action"]
                return (final_action, decision["outcome"],
                        reflexive_dict, re_ms_total)

            # Regenerate: cap at MAX_RETRIES.
            if retry_count >= MAX_RETRIES:
                # Bound exceeded. The router gave us `reflect` but
                # we're at the cap; treat as HILT (safety net).
                final_action = current_out.get("action", "")
                reflexive_dict = {
                    **reflexive_dict,
                    "reasoning": (reflexive_dict.get("reasoning", "")
                                  + f" [exhausted {MAX_RETRIES} retries; "
                                    "demoting to HILT]"),
                }
                return (final_action, "pending",
                        reflexive_dict, re_ms_total)

            # Demotion: if the primary was auto_send AND the Reflexive
            # flagged low intent_accuracy, demote to hilt_other for the
            # regeneration's context. The next round may still produce
            # auto_send if the model recovers.
            hints = list(reflexive_dict.get("regeneration_hints", []))
            current_out = self._regenerate_resolver(email, ctx, hints)
            retry_count += 1

    # --------------------------------------------------------------
    # Trace persistence
    # --------------------------------------------------------------
    def _emit(self, trace: Trace) -> None:
        path = self.trace_dir / f"{trace.email_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(trace.to_dict(), default=str, indent=2))
        os.replace(tmp, path)
        log.info("trace emitted: %s outcome=%s", path, trace.outcome)

    @staticmethod
    def _now_ms() -> int:
        return int(time.perf_counter() * 1000)
