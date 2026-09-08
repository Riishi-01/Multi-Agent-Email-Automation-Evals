"""tests/test_reflexive_routing.py — deterministic Python router.

Pure Python tests. No LLM calls. The router is the spec's
section 6 (Routing Rules) + section 7 (Confidence Bands).
"""
from __future__ import annotations

import unittest

import tests.conftest  # noqa: F401  (force DEMO mode)


class TestRoutingSideEffectOverride(unittest.TestCase):
    """Section 2: side-effect actions always go to HILT.

    In our workflow the side-effect actions are hilt_refund (money
    moves) and hilt_other (state change). Both are always HILT
    regardless of the reflexive's confidence band.
    """

    def _ro(self, conf, suggested="accept", action="hilt_refund"):
        return {
            "dimension_scores": {"side_effect_consent": conf},
            "confidence": conf,
            "reasoning": "test",
            "suggested_action": suggested,
            "regeneration_hints": [],
        }

    def test_hilt_refund_at_high_confidence_still_hilt(self):
        from src.agent.reflexive_routing import route_after_reflexive
        d = route_after_reflexive(
            primary_action="hilt_refund",
            reflexive_output=self._ro(conf=0.99),
            retry_count=0,
        )
        self.assertEqual(d["verdict"], "hilt")
        self.assertEqual(d["outcome"], "pending")
        self.assertEqual(d["action"], "hilt_refund")

    def test_hilt_other_still_hilt_at_low_confidence(self):
        from src.agent.reflexive_routing import route_after_reflexive
        d = route_after_reflexive(
            primary_action="hilt_other",
            reflexive_output=self._ro(conf=0.2, action="hilt_other"),
            retry_count=0,
        )
        self.assertEqual(d["verdict"], "hilt")

    def test_hilt_refund_still_hilt_at_medium_confidence(self):
        from src.agent.reflexive_routing import route_after_reflexive
        d = route_after_reflexive(
            primary_action="hilt_refund",
            reflexive_output=self._ro(conf=0.7),
            retry_count=0,
        )
        self.assertEqual(d["verdict"], "hilt")

    def test_hilt_other_still_hilt_at_high_confidence(self):
        from src.agent.reflexive_routing import route_after_reflexive
        d = route_after_reflexive(
            primary_action="hilt_other",
            reflexive_output=self._ro(conf=0.85, action="hilt_other"),
            retry_count=0,
        )
        self.assertEqual(d["verdict"], "hilt")


class TestRoutingConfidenceBands(unittest.TestCase):
    """Section 7: confidence bands for read-only actions."""

    def _ro(self, conf, suggested="accept"):
        return {
            "dimension_scores": {
                "intent_accuracy": conf, "faithfulness": conf,
                "policy_compliance": conf, "tone": conf,
                "side_effect_consent": 1.0,
            },
            "confidence": conf,
            "reasoning": "test",
            "suggested_action": suggested,
            "regeneration_hints": [],
        }

    def test_high_confidence_auto(self):
        from src.agent.reflexive_routing import route_after_reflexive
        for c in (0.80, 0.85, 0.95, 1.0):
            d = route_after_reflexive(
                primary_action="auto_send",
                reflexive_output=self._ro(c),
                retry_count=0,
            )
            self.assertEqual(d["verdict"], "accept",
                f"confidence={c} should accept")
            self.assertEqual(d["outcome"], "sent")

    def test_medium_confidence_hilt(self):
        from src.agent.reflexive_routing import route_after_reflexive
        for c in (0.65, 0.70, 0.75, 0.79):
            d = route_after_reflexive(
                primary_action="auto_send",
                reflexive_output=self._ro(c),
                retry_count=0,
            )
            self.assertEqual(d["verdict"], "hilt",
                f"confidence={c} should HILT")
            self.assertEqual(d["outcome"], "pending")

    def test_low_confidence_reflect_when_regenerate(self):
        from src.agent.reflexive_routing import route_after_reflexive
        d = route_after_reflexive(
            primary_action="auto_send",
            reflexive_output=self._ro(0.5, suggested="regenerate"),
            retry_count=0,
        )
        self.assertEqual(d["verdict"], "reflect")
        self.assertIsNone(d["outcome"])

    def test_low_confidence_escalate_immediate(self):
        from src.agent.reflexive_routing import route_after_reflexive
        d = route_after_reflexive(
            primary_action="auto_send",
            reflexive_output=self._ro(0.5, suggested="escalate"),
            retry_count=0,
        )
        self.assertEqual(d["verdict"], "escalate")
        self.assertEqual(d["outcome"], "human_queue")

    def test_exhausted_retries_demote_to_hilt(self):
        from src.agent.reflexive_routing import (
            route_after_reflexive, MAX_RETRIES,
        )
        d = route_after_reflexive(
            primary_action="auto_send",
            reflexive_output=self._ro(0.3, suggested="regenerate"),
            retry_count=MAX_RETRIES,
        )
        self.assertEqual(d["verdict"], "hilt")
        self.assertEqual(d["outcome"], "pending")

    def test_low_confidence_with_accept_still_hilt(self):
        from src.agent.reflexive_routing import route_after_reflexive
        # Reflexive accepted but confidence is low -> safety net demotes
        # to HILT rather than auto-sending a low-confidence response.
        d = route_after_reflexive(
            primary_action="auto_send",
            reflexive_output=self._ro(0.5, suggested="accept"),
            retry_count=0,
        )
        self.assertEqual(d["verdict"], "hilt")


class TestRoutingDecisionOrder(unittest.TestCase):
    """Section 16: structural overrides beat confidence bands."""

    def test_side_effect_beats_high_confidence(self):
        from src.agent.reflexive_routing import route_after_reflexive
        # confidence 0.99, action=hilt_refund -> must be HILT (side-effect
        # override wins, not the auto-resolve confidence band).
        d = route_after_reflexive(
            primary_action="hilt_refund",
            reflexive_output={
                "dimension_scores": {
                    "intent_accuracy": 0.99, "faithfulness": 0.99,
                    "policy_compliance": 0.99, "tone": 0.99,
                    "side_effect_consent": 0.99,
                },
                "confidence": 0.99,
                "reasoning": "test", "suggested_action": "accept",
                "regeneration_hints": [],
            },
            retry_count=0,
        )
        self.assertEqual(d["verdict"], "hilt")
        self.assertEqual(d["outcome"], "pending")

    def test_escalate_beats_high_confidence(self):
        from src.agent.reflexive_routing import route_after_reflexive
        d = route_after_reflexive(
            primary_action="auto_send",
            reflexive_output={
                "dimension_scores": {
                    "intent_accuracy": 0.99, "faithfulness": 0.99,
                    "policy_compliance": 0.99, "tone": 0.99,
                    "side_effect_consent": 0.99,
                },
                "confidence": 0.99,
                "reasoning": "test", "suggested_action": "escalate",
                "regeneration_hints": [],
            },
            retry_count=0,
        )
        self.assertEqual(d["verdict"], "escalate")
        self.assertEqual(d["outcome"], "human_queue")


class TestRoutingMaxRetries(unittest.TestCase):
    def test_max_retries_constant_is_2(self):
        from src.agent.reflexive_routing import MAX_RETRIES
        self.assertEqual(MAX_RETRIES, 2)
