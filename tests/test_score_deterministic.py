"""tests/test_score_deterministic.py — RED tests for the 5 deterministic checks.

Each check returns a PerMetric result: {"metric_id", "value", "passed",
"reason"} where value is bool/int/str depending on the metric.

The 5 deterministic checks:
  1. right_tools_called — every tool in action_sequence appears in
     retriever.tool_calls[].tool (and not necessarily vice-versa).
  2. linked_order_resolved — the order_id resolved by the retriever
     matches the eval-set's linked_order_id.
  3. correct_action — resolver.action matches the eval-set decision
     (auto_send | hilt_refund | hilt_other | escalate). hilt_refund
     and hilt_other both map to "hilt" in the gold set.
  4. intent_correct — resolver.intent matches the eval-set spec_intent.
  5. no_unnecessary_calls — retriever.tool_calls has fewer entries than
     MAX_TOOL_CALLS (6) AND lookup_customer was always first.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _trace(*, retriever: dict, resolver: dict) -> dict:
    return {
        "schema_version": "1.1",
        "email_id": "T1",
        "retriever": retriever,
        "resolver": resolver,
        "outcome": "sent",
    }


class TestRightToolsCalled(unittest.TestCase):
    def test_passes_when_all_action_tools_were_called(self):
        from src.agent.score import check_right_tools_called
        trace = _trace(
            retriever={"tool_calls": [
                {"tool": "lookup_customer"},
                {"tool": "lookup_order_by_sender_and_product"},
            ]},
            resolver={"action": "auto_send"},
        )
        golden = {"action_sequence": [
            {"tool": "lookup_customer", "args": {}},
            {"tool": "lookup_order_by_sender_and_product", "args": {}},
        ]}
        r = check_right_tools_called(trace, golden)
        self.assertTrue(r.passed)

    def test_fails_when_a_required_tool_was_missing(self):
        from src.agent.score import check_right_tools_called
        trace = _trace(
            retriever={"tool_calls": [{"tool": "lookup_customer"}]},
            resolver={"action": "auto_send"},
        )
        golden = {"action_sequence": [
            {"tool": "lookup_customer", "args": {}},
            {"tool": "lookup_order_by_sender_and_product", "args": {}},
        ]}
        r = check_right_tools_called(trace, golden)
        self.assertFalse(r.passed)
        self.assertIn("lookup_order_by_sender_and_product", r.reason)


class TestLinkedOrderResolved(unittest.TestCase):
    def test_passes_when_resolved_order_matches(self):
        from src.agent.score import check_linked_order_resolved
        trace = _trace(
            retriever={"order": {"found": True, "row": {"order_id": "BM200001"}}},
            resolver={"action": "auto_send"},
        )
        golden = {"linked_order_id": "BM200001"}
        r = check_linked_order_resolved(trace, golden)
        self.assertTrue(r.passed)

    def test_fails_on_mismatch(self):
        from src.agent.score import check_linked_order_resolved
        trace = _trace(
            retriever={"order": {"found": True, "row": {"order_id": "BM200001"}}},
            resolver={"action": "auto_send"},
        )
        golden = {"linked_order_id": "BM200002"}
        r = check_linked_order_resolved(trace, golden)
        self.assertFalse(r.passed)

    def test_passes_when_no_order_expected(self):
        from src.agent.score import check_linked_order_resolved
        trace = _trace(
            retriever={"order": {"found": False, "row": None}},
            resolver={"action": "auto_send"},
        )
        golden = {"linked_order_id": ""}            # no order expected
        r = check_linked_order_resolved(trace, golden)
        self.assertTrue(r.passed)


class TestCorrectAction(unittest.TestCase):
    def test_auto_send_matches(self):
        from src.agent.score import check_correct_action
        trace = _trace(
            retriever={},
            resolver={"action": "auto_send"},
        )
        golden = {"decision": "auto_send"}
        self.assertTrue(check_correct_action(trace, golden).passed)

    def test_hilt_refund_matches_hilt_refund_decision(self):
        from src.agent.score import check_correct_action
        trace = _trace(
            retriever={},
            resolver={"action": "hilt_refund"},
        )
        golden = {"decision": "hilt_refund"}
        self.assertTrue(check_correct_action(trace, golden).passed)

    def test_hilt_other_matches_hilt_other_decision(self):
        from src.agent.score import check_correct_action
        trace = _trace(
            retriever={},
            resolver={"action": "hilt_other"},
        )
        golden = {"decision": "hilt_other"}
        self.assertTrue(check_correct_action(trace, golden).passed)

    def test_mismatch(self):
        from src.agent.score import check_correct_action
        trace = _trace(retriever={}, resolver={"action": "auto_send"})
        golden = {"decision": "hilt_refund"}
        self.assertFalse(check_correct_action(trace, golden).passed)


class TestCorrectActionCategory(unittest.TestCase):
    """Workflow-level categorical check: hilt_refund and hilt_other are
    both 'hilt' (same outcome, same operator queue). Independent of
    the strict 4-way `correct_action` which preserves the bundle's
    sub-action distinction."""

    def test_hilt_refund_matches_hilt_other_gold(self):
        from src.agent.score import check_correct_action_category
        trace = _trace(retriever={}, resolver={"action": "hilt_refund"})
        golden = {"decision": "hilt_other"}
        r = check_correct_action_category(trace, golden)
        self.assertTrue(r.passed)
        self.assertEqual(r.value["actual_cat"], "hilt")
        self.assertEqual(r.value["expected_cat"], "hilt")

    def test_hilt_other_matches_hilt_refund_gold(self):
        from src.agent.score import check_correct_action_category
        trace = _trace(retriever={}, resolver={"action": "hilt_other"})
        golden = {"decision": "hilt_refund"}
        r = check_correct_action_category(trace, golden)
        self.assertTrue(r.passed)

    def test_hilt_refund_matches_hilt_refund(self):
        from src.agent.score import check_correct_action_category
        trace = _trace(retriever={}, resolver={"action": "hilt_refund"})
        golden = {"decision": "hilt_refund"}
        self.assertTrue(check_correct_action_category(trace, golden).passed)

    def test_auto_send_still_passes(self):
        from src.agent.score import check_correct_action_category
        trace = _trace(retriever={}, resolver={"action": "auto_send"})
        golden = {"decision": "auto_send"}
        self.assertTrue(check_correct_action_category(trace, golden).passed)

    def test_escalate_still_passes(self):
        from src.agent.score import check_correct_action_category
        trace = _trace(retriever={}, resolver={"action": "escalate"})
        golden = {"decision": "escalate"}
        self.assertTrue(check_correct_action_category(trace, golden).passed)

    def test_hilt_does_not_match_auto_send(self):
        from src.agent.score import check_correct_action_category
        trace = _trace(retriever={}, resolver={"action": "hilt_refund"})
        golden = {"decision": "auto_send"}
        self.assertFalse(check_correct_action_category(trace, golden).passed)

    def test_correct_action_still_strict_for_4_way(self):
        """Guard: the bundle's strict 4-way check is unchanged."""
        from src.agent.score import check_correct_action
        trace = _trace(retriever={}, resolver={"action": "hilt_refund"})
        golden = {"decision": "hilt_other"}
        # Strict 4-way: hilt_refund != hilt_other
        self.assertFalse(check_correct_action(trace, golden).passed)

    def test_registered_in_deterministic_checks(self):
        from src.agent.score import DETERMINISTIC_CHECKS
        ids = [c.__name__ for c in DETERMINISTIC_CHECKS]
        self.assertIn("check_correct_action_category", ids)
        self.assertIn("check_correct_action", ids)


class TestActionCategoryMapping(unittest.TestCase):
    """Direct unit test of the categorical mapping function."""

    def test_auto_send(self):
        from src.agent.score import _action_category
        self.assertEqual(_action_category("auto_send"), "auto_send")

    def test_hilt_refund_to_hilt(self):
        from src.agent.score import _action_category
        self.assertEqual(_action_category("hilt_refund"), "hilt")

    def test_hilt_other_to_hilt(self):
        from src.agent.score import _action_category
        self.assertEqual(_action_category("hilt_other"), "hilt")

    def test_escalate(self):
        from src.agent.score import _action_category
        self.assertEqual(_action_category("escalate"), "escalate")

    def test_unknown_defaults_to_string(self):
        from src.agent.score import _action_category
        self.assertEqual(_action_category("some_new_action"), "some_new_action")


class TestIntentCorrect(unittest.TestCase):
    def test_intent_matches(self):
        from src.agent.score import check_intent_correct
        trace = _trace(retriever={}, resolver={"intent": "logistic"})
        golden = {"spec_intent": "logistic"}
        self.assertTrue(check_intent_correct(trace, golden).passed)

    def test_intent_mismatch(self):
        from src.agent.score import check_intent_correct
        trace = _trace(retriever={}, resolver={"intent": "info"})
        golden = {"spec_intent": "refund"}
        self.assertFalse(check_intent_correct(trace, golden).passed)


class TestNoUnnecessaryCalls(unittest.TestCase):
    def test_passes_when_lookup_customer_is_first(self):
        from src.agent.score import check_no_unnecessary_calls
        trace = _trace(
            retriever={"tool_calls": [
                {"tool": "lookup_customer"},
                {"tool": "lookup_order_by_sender_and_product"},
                {"tool": "get_product_details"},
            ]},
            resolver={},
        )
        self.assertTrue(check_no_unnecessary_calls(trace, {}).passed)

    def test_fails_when_lookup_customer_not_first(self):
        from src.agent.score import check_no_unnecessary_calls
        trace = _trace(
            retriever={"tool_calls": [
                {"tool": "lookup_order_by_id"},
                {"tool": "lookup_customer"},
            ]},
            resolver={},
        )
        self.assertFalse(check_no_unnecessary_calls(trace, {}).passed)

    def test_fails_when_too_many_calls(self):
        from src.agent.score import check_no_unnecessary_calls
        trace = _trace(
            retriever={"tool_calls": [
                {"tool": "lookup_customer"},
                {"tool": "t2"}, {"tool": "t3"}, {"tool": "t4"},
                {"tool": "t5"}, {"tool": "t6"}, {"tool": "t7"},
            ]},
            resolver={},
        )
        self.assertFalse(check_no_unnecessary_calls(trace, {}).passed)


class TestDeterministicDispatch(unittest.TestCase):
    def test_score_traces_runs_all_nine(self):
        """6 deterministic + 3 field/regex checks all fire in score_traces."""
        from src.agent.score import score_traces
        trace = _trace(
            retriever={"order": {"found": True, "row": {"order_id": "BM200001"}},
                       "tool_calls": [{"tool": "lookup_customer"},
                                       {"tool": "lookup_order_by_sender_and_product"}],
                       "policies": [],
                       "payments": []},
            resolver={"action": "auto_send", "intent": "logistic",
                      "draft": "Thanks for reaching out."},
        )
        golden = {
            "linked_order_id": "BM200001",
            "decision": "auto_send",
            "spec_intent": "logistic",
            "action_sequence": [
                {"tool": "lookup_customer", "args": {}},
                {"tool": "lookup_order_by_sender_and_product", "args": {}},
            ],
        }
        scores = score_traces([(trace, golden)])
        self.assertEqual(len(scores), 1)
        ids = {m.metric_id for m in scores[0].metrics}
        self.assertSetEqual(
            ids,
            {"right_tools_called", "linked_order_resolved",
             "correct_action", "correct_action_category",
             "intent_correct", "no_unnecessary_calls",
             "no_pii_echo", "no_fabricated_amounts", "cites_policy_clause"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
