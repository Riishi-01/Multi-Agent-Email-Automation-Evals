"""src/agent/resolver-reflexive/__init__.py — Resolver Reflexive Agent.

The Reflexive is a *generic* verification layer for email-support
agents. It reviews another agent's output, scores it on a configurable
rubric, and emits a verdict (accept / regenerate / escalate) plus
confidence = min(dimension_scores.values()).

It is reusable: pass any agent's output (resolver, retriever, future
agents) and any rubric, and the Reflexive produces a structured
ReflexiveOutput. The workflow decides what to do with the verdict
(deterministic Python routing, not the LLM's call).

This is the **resolver_reflexive_agent** (Phase 5F). It builds on top
of the existing resolver and retriever agents without duplicating
their functionality. The Reflexive has its own 5 modular prompt
files (role, tools, few_shot_examples, state_examples, guardrails)
plus a VERSION stamp, composed by PromptBuilder at construction.
"""
from .agent_resolver_reflexive import (
    ReflexiveOutput,
    ResolverReflexive,
    DEFAULT_RESOLVER_RUBRIC,
    SIDE_EFFECT_ACTIONS,
)

__all__ = [
    "ReflexiveOutput",
    "ResolverReflexive",
    "DEFAULT_RESOLVER_RUBRIC",
    "SIDE_EFFECT_ACTIONS",
]
