"""src/agent/reflexive_routing.py — deterministic routing after the Reflexive.

Pure Python, no LLM. Implements the spec's section 6 (Routing Rules)
and section 7 (Confidence Bands), in the order from section 16
(Important Ordering of Decisions):

  1. Structural safety overrides (no chunks + policy-bound question
     -> escalate, etc.; handled in retrieval_checks.py before this
     function is called)
  2. Side-effect override -> always HILT
  3. Hard escalation conditions (reflexive said "escalate")
  4. Confidence band (read-only actions)
  5. Reflexive suggested_action for low confidence
  6. Retry count

This module is the **only** place that decides the final workflow
outcome from the Reflexive's verdict. The LLM only does semantic
scoring + fixable/unfixable classification.
"""
from __future__ import annotations

from typing import Any


# Side-effect actions always go to HILT regardless of confidence.
# Mirror of the ResolverReflexive constant. Kept here so this module
# has no cross-dependency on the agent module (the router must be
# importable without dragging in the OpenAI client factory).
# The workflow's side-effect actions are hilt_refund and hilt_other
# (which together map to the spec's "refund / replacement / cancel /
# address_change / goodwill"). They always go to HILT regardless of
# the reflexive's confidence.
SIDE_EFFECT_ACTIONS: frozenset[str] = frozenset({
    "hilt_refund", "hilt_other",
})

# Max retries (spec section 8). Used by the workflow's regenerate loop.
MAX_RETRIES: int = 2


def route_after_reflexive(
    *,
    primary_action: str,
    reflexive_output: dict[str, Any],
    retry_count: int,
) -> dict[str, Any]:
    """Deterministic routing based on the Reflexive's output.

    Returns a dict with keys:
      - verdict:     "accept" | "hilt" | "escalate" | "reflect"
      - outcome:     "sent" | "pending" | "human_queue" | None
      - action:      str  (the final action; may equal primary_action
                            or be demoted to "hilt_other" for safety)
      - rationale:   str  (one sentence explaining the route)

    The Reflexive's `suggested_action` field drives `reflect` vs
    `escalate` for low-confidence cases. The confidence band drives
    `accept` vs `hilt` for read-only cases. Side-effect cases are
    always `hilt` (operator review).
    """
    confidence = float(reflexive_output.get("confidence", 0.0) or 0.0)
    suggested   = reflexive_output.get("suggested_action", "accept")

    # 1+2: side-effect override
    if primary_action in SIDE_EFFECT_ACTIONS:
        return {
            "verdict":   "hilt",
            "outcome":   "pending",
            "action":    primary_action,
            "rationale": ("side-effect action requires human review "
                          f"(confidence={confidence:.2f})"),
        }

    # 3: hard escalation (reflexive explicitly said unfixable)
    if suggested == "escalate":
        return {
            "verdict":   "escalate",
            "outcome":   "human_queue",
            "action":    primary_action,
            "rationale": "Reflexive marked this as unfixable; escalate",
        }

    # 4: confidence band for read-only actions
    if confidence >= 0.80:
        return {
            "verdict":   "accept",
            "outcome":   "sent",
            "action":    primary_action,
            "rationale": f"high confidence ({confidence:.2f}); accept",
        }
    if confidence >= 0.65:
        return {
            "verdict":   "hilt",
            "outcome":   "pending",
            "action":    primary_action,
            "rationale": f"medium confidence ({confidence:.2f}); HILT",
        }

    # 5+6: low confidence — use the Reflexive's suggested_action
    if suggested == "regenerate" and retry_count < MAX_RETRIES:
        return {
            "verdict":   "reflect",
            "outcome":   None,           # the workflow will retry
            "action":    primary_action,
            "rationale": (f"low confidence ({confidence:.2f}); "
                          f"retry {retry_count + 1}/{MAX_RETRIES}"),
        }
    if suggested == "regenerate" and retry_count >= MAX_RETRIES:
        return {
            "verdict":   "hilt",
            "outcome":   "pending",
            "action":    primary_action,
            "rationale": (f"exhausted {MAX_RETRIES} retries; HILT "
                          f"(confidence was {confidence:.2f})"),
        }
    if suggested == "accept":
        # Reflexive accepted, but confidence is low. Demote to HILT
        # for safety rather than auto_send a low-confidence response.
        return {
            "verdict":   "hilt",
            "outcome":   "pending",
            "action":    primary_action,
            "rationale": (f"low confidence ({confidence:.2f}) but "
                          "Reflexive accepted; HILT for safety"),
        }

    # Defensive default: anything else (malformed reflexive output)
    # routes to HILT.
    return {
        "verdict":   "hilt",
        "outcome":   "pending",
        "action":    primary_action,
        "rationale": "default fallback (malformed reflexive output); HILT",
    }
