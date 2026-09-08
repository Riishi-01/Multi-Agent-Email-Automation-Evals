"""src/agent/types.py — shared dataclasses for workflow orchestration.

Keeps `workflow.py` and `scripts/run_one.py` from each maintaining
their own Email/Trace. Single source of truth for the wire shape.

Phase 4C: Trace schema version 1.1.
  - Adds `retriever.tool_calls[]` so the scorer can audit `right_tools_called`.
  - Adds `resolver.decision` (the 4-value bundle enum).
  - Adds `resolver.hilt_reason` (7-field object on hilt_* actions).
  - Adds `retriever.policies[]` for clause-precise citation scoring.

Phase 5F: Trace schema version 1.2.
  - Adds `reflexive` (ReflexiveOutput.to_dict() — 5-dim scores +
    confidence + reasoning + suggested_action + regeneration_hints).
  - Adds `final_action` (the action after any reflexive demotion;
    e.g. primary hilt_refund demoted to hilt_other when the reflexive
    flags intent_accuracy < 0.65).
  - `schema_version` bumped to "1.2".
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Email:
    """Three runtime inputs of an inbound email.

    Note: per build.yaml invariant #1, `order_id`, `register_email`,
    and `order_item` columns from `emails.tsv` are evaluation metadata;
    they NEVER appear in this dataclass (and never reach the agent).
    """
    subject: str
    body: str
    sender_email: str
    notes: Optional[str] = None     # free-form metadata; not sent to the agent


@dataclass
class ToolLogEntry:
    tool: str
    args: dict
    duration_ms: int
    result_keys: list[str] = field(default_factory=list)
    similarity: Optional[float] = None
    error: Optional[str] = None


@dataclass
class Trace:
    """Wire shape of `data/runs/<email_id>.json`.

    Schema version `1.2`. Phase 4C eval runner reads this; Phase 5F
    reflexive layer writes the new `reflexive` and `final_action`
    fields.

    Field order matters for the dataclass: required fields must
    come before defaulted ones. So `outcome` precedes `final_action`
    (which defaults to "") and `error` (which defaults to None).
    """
    schema_version: str
    email_id: str
    captured_at: str
    input: dict                                # explicit subset of Email
    retriever: dict                            # RetrieverContext.to_dict()
    resolver: dict                             # ResolverResult.to_dict()
    outcome: str                               # "sent" | "pending" | "human_queue" | "error"
    timing_ms: dict
    reflexive: dict = field(default_factory=dict)   # Phase 5F
    final_action: str = ""                       # Phase 5F (post-reflexive demotion)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)
