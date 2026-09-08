"""tests/conftest.py — force deterministic fallback for the test suite.

Runs at collection time (before any test module is imported) so every
test, regardless of which API keys are set in .env, uses deterministic
fallbacks for the AGENTS (resolver, retriever, reflexive). The embedding
API key is preserved so RAG retrieval still uses real OpenAI embeddings
(matching the production path).

Production code paths (scripts/run_eval.py, scripts/run_one.py, the
operator-driven CLI) are unaffected.
"""
import os


# Keep OPENAI_API_KEY set so RAG embeddings match production.
# The agents' `_openai()` factories are patched to return None to
# force the deterministic branch for the LLM path.

from src.agent._test_mode import enter_demo_mode, exit_demo_mode


def _patched_enter_demo_mode() -> None:
    """Same as src.agent._test_mode.enter_demo_mode but preserves the
    embedding API key so RAG retrieval uses real OpenAI embeddings."""
    import src.agent.resolver_agent.agent_resolver as _r
    import src.agent.retriever_agent.agent_retriever as _t
    import src.agent.resolver_reflexive.agent_resolver_reflexive as _rf

    # Patch the LLM-client factories to return None (deterministic path).
    _r._openai = lambda: None  # type: ignore[assignment]
    _t._openai = lambda: None  # type: ignore[assignment]
    _rf._openai = lambda: None  # type: ignore[assignment]

    # Clear the JUDGE_* env vars (the judge layer is always "not configured"
    # in tests).
    stash = getattr(_patched_enter_demo_mode, "_stash", None)
    if stash is None:
        stash = {}
        for k in ("JUDGE_BASE_URL", "JUDGE_MODEL", "JUDGE_API_KEY"):
            stash[k] = os.environ.get(k)
            os.environ.pop(k, None)
        _patched_enter_demo_mode._stash = stash


_patched_enter_demo_mode()


import pytest


@pytest.fixture(autouse=True)
def _force_demo_mode_per_test():
    """Per-test safety net: re-patch the agent factories in case any
    fixture or import between session start and now restored them."""
    import src.agent.resolver_agent.agent_resolver as _r
    import src.agent.retriever_agent.agent_retriever as _t
    import src.agent.resolver_reflexive.agent_resolver_reflexive as _rf
    _r._openai = lambda: None  # type: ignore[assignment]
    _t._openai = lambda: None  # type: ignore[assignment]
    _rf._openai = lambda: None  # type: ignore[assignment]
    # Restore env for next iteration (don't disturb OPENAI_API_KEY).
    yield


# Restore env on interpreter shutdown.
import atexit
atexit.register(exit_demo_mode)

