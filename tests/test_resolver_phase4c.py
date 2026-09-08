"""tests/test_resolver_phase4c.py — RED tests for the 4-decision + hilt_reason resolver.

Bundle vocabulary (per data/completeBytemartEvalset/BytemartEvals.yaml):
  - Decisions: auto_send | hilt_refund | hilt_other | escalate
  - hilt_reason: {policy_clause, reason_short, reason_long,
                   recommended_action, action_target, urgency,
                   human_skill_required}
  - self_check: {policy_compliant, entities_correct, state_change_implied, tone}

DEMO mode only (no OPENAI_API_KEY); the schema validator runs against the
DEMO classifier's output too.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


class TestFourDecisionEnum(unittest.TestCase):
    def test_action_enum_includes_four(self):
        from src.agent.resolver_agent.agent_resolver import ACTION_ENUM
        self.assertSetEqual(
            set(ACTION_ENUM),
            {"auto_send", "hilt_refund", "hilt_other", "escalate"},
        )


class TestAutoSendKeepsShape(unittest.TestCase):
    """E1, E2, E10, E11, etc.: simple info inquiry → auto_send, no hilt_reason."""

    def test_info_inquiry_emits_auto_send(self):
        from src.agent.resolver_agent.agent_resolver import ResolverAgent
        agent = ResolverAgent()
        ctx = {
            "customer": {"found": True, "row": {"full_name": "Sanya", "email": "sanya.delhi@gmail.com"}},
            "order":    {"found": True, "row": {"order_id": "BM200001"}},
            "payments": [],
            "products": [],
            "policies": [],
        }
        out = agent.run(subject="PS5 delivery", body="When will my PS5 arrive?",
                        sender_email="sanya.delhi@gmail.com", context=ctx)
        self.assertEqual(out.action, "auto_send")
        self.assertIsNone(out.hilt_reason)
        self.assertIsNotNone(out.draft)
        self.assertNotIn("[PENDING APPROVAL]", out.draft)


class TestHiltRefundAddsReason(unittest.TestCase):
    """E22, E30, E24, ES-034: refund-producing hilt → hilt_refund + 7-field hilt_reason."""

    def test_refund_request_emits_hilt_refund(self):
        from src.agent.resolver_agent.agent_resolver import ResolverAgent
        agent = ResolverAgent()
        ctx = {
            "customer": {"found": True, "row": {"full_name": "Anjali", "email": "anjalisingh.hyd@gmail.com"}},
            "order":    {"found": True, "row": {"order_id": "BM200009"}},
            "payments": [{"status": "success", "amount": 18999, "order_id": "BM200009"}],
            "products": [],
            "policies": [],
        }
        out = agent.run(subject="Refund request",
                        body="My Razer BlackShark is defective. I want a refund.",
                        sender_email="anjalisingh.hyd@gmail.com", context=ctx)
        self.assertEqual(out.action, "hilt_refund")
        self.assertIsNotNone(out.hilt_reason)
        # All 7 fields must be present and non-empty.
        for f in ("policy_clause", "reason_short", "reason_long",
                  "recommended_action", "action_target", "urgency",
                  "human_skill_required"):
            self.assertTrue(getattr(out.hilt_reason, f),
                            f"hilt_reason.{f} empty")

    def test_hilt_refund_draft_has_placeholder(self):
        from src.agent.resolver_agent.agent_resolver import ResolverAgent
        agent = ResolverAgent()
        ctx = {
            "customer": {"found": True, "row": {"full_name": "Anjali"}},
            "order":    {"found": True, "row": {"order_id": "BM200009"}},
            "payments": [], "products": [], "policies": [],
        }
        out = agent.run(subject="Refund", body="I want a refund.",
                        sender_email="a@b.com", context=ctx)
        self.assertEqual(out.action, "hilt_refund")
        self.assertIn("[PENDING APPROVAL]", out.draft)


class TestHiltOtherNoRefund(unittest.TestCase):
    """E18, E19, E20, E21: defective/wrong item returns → hilt_other (no refund)."""

    def test_defective_return_emits_hilt_other(self):
        from src.agent.resolver_agent.agent_resolver import ResolverAgent
        agent = ResolverAgent()
        ctx = {
            "customer": {"found": True, "row": {"full_name": "Rishit"}},
            "order":    {"found": True, "row": {"order_id": "BM728349"}},
            "payments": [], "products": [], "policies": [],
        }
        out = agent.run(subject="Damaged on arrival",
                        body="My PS5 arrived damaged. I want a replacement.",
                        sender_email="rishit1@gmail.com", context=ctx)
        self.assertEqual(out.action, "hilt_other")
        self.assertIsNotNone(out.hilt_reason)
        self.assertIn("[PENDING APPROVAL]", out.draft)


class TestEscalateOnlyWhenNoIdentity(unittest.TestCase):
    def test_escalate_emitted_when_no_identity(self):
        from src.agent.resolver_agent.agent_resolver import ResolverAgent
        agent = ResolverAgent()
        ctx = {
            "customer": {"found": False, "row": None},
            "order":    {"found": False, "row": None},
            "payments": [],
            "products": [],
            "policies": [],
        }
        out = agent.run(subject="Erasure", body="Erase my data under DPDPA.",
                        sender_email="", context=ctx)
        self.assertEqual(out.action, "escalate")
        self.assertIsNone(out.draft)
        self.assertIsNone(out.hilt_reason)


class TestSelfCheckFields(unittest.TestCase):
    def test_self_check_carries_state_change_flag(self):
        from src.agent.resolver_agent.agent_resolver import ResolverAgent
        agent = ResolverAgent()
        ctx = {
            "customer": {"found": True, "row": {"full_name": "Sanya"}},
            "order":    {"found": True, "row": {"order_id": "BM200001"}},
            "payments": [], "products": [], "policies": [],
        }
        out = agent.run(subject="X", body="Y", sender_email="sanya.delhi@gmail.com",
                        context=ctx)
        for k in ("policy_compliant", "entities_correct",
                  "state_change_implied", "tone"):
            self.assertIn(k, out.self_check)


class TestUsedEntities(unittest.TestCase):
    def test_used_entities_includes_order_id_and_skus(self):
        from src.agent.resolver_agent.agent_resolver import ResolverAgent
        agent = ResolverAgent()
        ctx = {
            "customer": {"found": True, "row": {"full_name": "Sanya"}},
            "order":    {"found": True, "row": {"order_id": "BM200001",
                                                 "items": [{"sku": "PS5-DISC-001"}]}},
            "payments": [], "products": [], "policies": [],
        }
        out = agent.run(subject="X", body="Y", sender_email="sanya.delhi@gmail.com",
                        context=ctx)
        self.assertEqual(out.used_entities["order_id"], "BM200001")
        self.assertIn("PS5-DISC-001", out.used_entities["product_names"])


class TestHiltReasonSchema(unittest.TestCase):
    def test_hilt_reason_is_dataclass(self):
        from src.agent.resolver_agent.agent_resolver import HiltReason
        hr = HiltReason(
            policy_clause="refund §3",
            reason_short="duplicate charge",
            reason_long="Customer double-charged; refund failed ₹5499.",
            recommended_action="approve_refund_Rs5499",
            action_target="refund_execution",
            urgency="high",
            human_skill_required="tier_2_support",
        )
        self.assertEqual(hr.urgency, "high")
        d = hr.to_dict() if hasattr(hr, "to_dict") else hr.__dict__
        self.assertEqual(d["policy_clause"], "refund §3")


class TestToDict(unittest.TestCase):
    def test_to_dict_round_trip(self):
        from src.agent.resolver_agent.agent_resolver import ResolverResult
        r = ResolverResult(
            intent="info_seeking", action="auto_send", draft="Hi",
            self_check={"tone": "polite"}, reasoning="...",
            used_entities={"order_id": "BM1"},
        )
        d = r.to_dict()
        for k in ("intent", "action", "draft", "self_check",
                  "reasoning", "used_entities", "hilt_reason"):
            self.assertIn(k, d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
