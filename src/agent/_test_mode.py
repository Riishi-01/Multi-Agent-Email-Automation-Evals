"""src/agent/_test_mode.py — force deterministic fallback for the test suite.

Without this, OPENAI_API_KEY in .env causes Workflow.run() to call the
real OpenAI client, and tests/test_eval_set.py issues ~100 LLM calls
per run (suite takes minutes instead of seconds).

`enter_demo_mode()` patches the lazy LLM-client factories to return
None AND clears the JUDGE_* env vars. With this, every test path is
the deterministic fallback branch; the judge is always "not configured"
so its tests assert the unscored path.

Production code paths (scripts/run_eval.py, scripts/run_one.py, the
operator-driven CLI) are unaffected.
"""
from __future__ import annotations

import os


def enter_demo_mode() -> None:
    """Patch the lazy _openai factories to return None (deterministic fallback) +
    clear the JUDGE_* env vars so the judge layer reports unscored.

    The original env values are stashed on a module attribute so
    `exit_demo_mode()` can restore them (used by the test finalizer).
    """
    from src.rag import _openai as _rag_openai
    from src.agent.resolver_agent import agent_resolver as _r
    from src.agent.retriever_agent import agent_retriever as _t
    from src.agent.resolver_reflexive import (
        agent_resolver_reflexive as _rf,
    )

    # 1) Force the OpenAI client factories to None -> deterministic fallback.
    _r._openai = lambda: None         # type: ignore[assignment]
    _t._openai = lambda: None         # type: ignore[assignment]
    _rf._openai = lambda: None         # type: ignore[assignment]
    # src.rag.embed_texts uses _rag._OPENAI directly; clear it.
    import src.rag as _rag
    _rag._OPENAI = None

    # 2) Clear JUDGE_* env vars (stashing originals for restoration).
    stash = getattr(enter_demo_mode, "_stash", None)
    if stash is None:
        stash = {}
        for k in ("JUDGE_BASE_URL", "JUDGE_MODEL", "JUDGE_API_KEY"):
            stash[k] = os.environ.get(k)
            os.environ.pop(k, None)
        enter_demo_mode._stash = stash

    # Also clear OPENAI_API_KEY so the OpenAI client factories can't
    # accidentally re-construct from env after our patch (defensive;
    # already neutralized by the factory patches above).
    os.environ.pop("OPENAI_API_KEY", None)


def exit_demo_mode() -> None:
    """Restore the env values stashed by enter_demo_mode()."""
    stash = getattr(enter_demo_mode, "_stash", None)
    if not stash:
        return
    for k, v in stash.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    enter_demo_mode._stash = None
