#!/usr/bin/env python3
"""
run_one.py — manual single-email harness for the AM-003 workflow.

Usage:
    # Real OpenAI + OpenRouter judge (production path)
    python scripts/run_one.py \\
        --email-id TEST-1 \\
        --subject "Order missing" \\
        --email-content "Where is my order?" \\
        --sender-email "sanya.delhi@gmail.com"

    # Deterministic-fallback mode: no LLM calls, no API keys required
    python scripts/run_one.py --deterministic-fallback \\
        --email-id TEST-DEMO \\
        --subject "PS5 missing" \\
        --email-content "Where is my PS5?" \\
        --sender-email "sanya.delhi@gmail.com"

    [--trace-dir data/runs]

Writes a trace JSON to <trace_dir>/<email_id>.json and prints the
resolver output to stdout. Single ad-hoc input — does NOT read
emails.tsv (the eval harness in AM-004 does that; this script is for
sanity-testing one email at a time).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Load .env so OPENAI_API_KEY is available for embedding-based RAG.
# (Without this, the script falls through to hash embeddings when run
# outside of pytest.)
try:
    from dotenv import load_dotenv
    _env = REPO_ROOT / ".env"
    if _env.exists():
        load_dotenv(_env, override=False)
except ImportError:
    pass


def main() -> int:
    ap = argparse.ArgumentParser(description="AM-003 workflow single-email harness.")
    ap.add_argument("--email-id", required=True,
                    help="Identifier written to the trace JSON and used as filename.")
    ap.add_argument("--subject", required=True)
    ap.add_argument("--email-content", required=True)
    ap.add_argument("--sender-email", required=True)
    ap.add_argument("--trace-dir", default="data/runs",
                    help="Where to write the trace JSON (default: data/runs).")
    ap.add_argument("--deterministic-fallback", action="store_true",
                    help="Run in deterministic-fallback mode (no LLM calls). Required when "
                         "OPENAI_API_KEY is unset; speeds up smoke tests.")
    args = ap.parse_args()

    from src.agent.workflow import Workflow
    from src.agent.types import Email

    email = Email(
        subject=args.subject,
        body=args.email_content,
        sender_email=args.sender_email,
    )

    if args.deterministic_fallback:
        # Deterministic-fallback mode: force the agents to use their
        # deterministic branches (no LLM calls) by patching each agent's
        # `_openai()` factory to return None directly. We DO NOT clear
        # `OPENAI_API_KEY` because RAG embeddings still benefit from
        # real OpenAI embeddings — the agent fallback (resolver /
        # retriever / reflexive) is what we want deterministic for, not
        # the embedding model. The judge key is cleared because we
        # don't have one configured for this script.
        os.environ["JUDGE_API_KEY"] = ""
        import src.agent.resolver_agent.agent_resolver as _r
        import src.agent.retriever_agent.agent_retriever as _t
        import src.agent.resolver_reflexive.agent_resolver_reflexive as _rf
        _r._openai = lambda: None  # type: ignore[assignment]
        _t._openai = lambda: None  # type: ignore[assignment]
        _rf._openai = lambda: None  # type: ignore[assignment]

    wf = Workflow(trace_dir=args.trace_dir)
    trace = wf.run(args.email_id, email)

    # Pretty-print resolver output to stdout for inspection.
    print("=" * 60)
    print(f"email_id:      {trace.email_id}")
    print(f"outcome:       {trace.outcome}")
    print(f"error:         {trace.error!r}")
    print(f"timings_ms:    {trace.timing_ms}")
    print(f"resolver.action: {trace.resolver.get('action')!r}")
    print(f"resolver.intent: {trace.resolver.get('intent')!r}")
    print(f"resolver.self_check: {trace.resolver.get('self_check')!r}")
    print(f"resolver.reasoning:   {trace.resolver.get('reasoning')!r}")
    if trace.resolver.get("draft"):
        draft = trace.resolver["draft"]
        print(f"\n--- draft ({len(draft)} chars) ---")
        print(draft)
        print("--- end draft ---")
    # Phase 5F: print the Reflexive's verdict
    reflexive = getattr(trace, "reflexive", None) or {}
    if reflexive:
        print(f"\nreflexive.confidence:  {reflexive.get('confidence', 0):.2f}")
        print(f"reflexive.verdict:      {reflexive.get('suggested_action', '?')}")
        print(f"reflexive.malformed:    {reflexive.get('malformed', False)}")
        scores = reflexive.get("dimension_scores") or {}
        if scores:
            worst = min(scores.items(), key=lambda kv: kv[1])
            print(f"reflexive.worst_dim:    {worst[0]}={worst[1]:.2f}")
        if reflexive.get("reasoning"):
            print(f"reflexive.reasoning:    {reflexive['reasoning'][:200]}")
    final_action = getattr(trace, "final_action", "") or ""
    if final_action and final_action != trace.resolver.get("action"):
        print(f"final_action (post-reflexive): {final_action!r}")
    print(f"\ntrace written: {args.trace_dir}/{trace.email_id}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
